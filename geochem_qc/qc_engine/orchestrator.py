"""
orchestrator.py — GeoQC Pro brain: receives mode + files, runs full pipeline.
"""

import json
import traceback
from pathlib import Path
from datetime import datetime

import pandas as pd

BASE_DIR = Path(__file__).parent.parent
OUTPUT_DIR = BASE_DIR / "output"

# ─── Imports ─────────────────────────────────────────────────────────────────
from qc_engine.core.parser import parse_file, parse_multiple_files
from qc_engine.core.merger import parse_dispatch, merge_dispatch_and_results
from qc_engine.core.separator import separate_qc, identify_lab_qc
from qc_engine.core.cleaner import clean_data
from qc_engine.core.completeness import score_completeness
from qc_engine.core.pre_analysis import run_pre_analysis
from qc_engine.qc.duplicates import run_duplicates_qc
from qc_engine.qc.blanks import run_blanks_qc
from qc_engine.qc.standards import run_standards_qc
from qc_engine.qc.qc_lab_engine import validate_lab_qc
from qc_engine.memory.historique import save_batch, list_batches
from qc_engine.output.xlsx_writer import write_xlsx
from qc_engine.output.pdf_writer import write_pdf


def run_pipeline(
    mode: str,
    analytical_files: list[str],
    dispatch_file: str = None,
    conflict_resolutions: dict = None,
    progress_callback=None,
    zone: str = None,
) -> dict:
    """
    Full QC pipeline.

    Args:
        mode: 'A' through 'H'
        analytical_files: list of file paths
        dispatch_file: optional dispatch Excel/CSV
        conflict_resolutions: dict of {sample_id: resolution_choice}
        progress_callback: callable(pct, message) for live updates
        zone: optional zone override

    Returns:
        dict with output files, qc_results, summary, errors
    """

    def _progress(pct: int, msg: str):
        if progress_callback:
            progress_callback(pct, msg)

    log = []
    errors = []
    output_files = {}

    try:
        # ── STEP 1: Parse files ──────────────────────────────────────────────
        _progress(5, "Parsing analytical files...")
        parsed = parse_multiple_files(analytical_files)
        log.extend(parsed.get('log', []))

        if parsed['total_records'] == 0:
            return {
                'success': False,
                'error': 'No records parsed from provided files',
                'log': log
            }

        # ── STEP 2: Parse dispatch ───────────────────────────────────────────
        _progress(15, "Reading dispatch...")
        dispatch_df = None
        if dispatch_file:
            dispatch_df = parse_dispatch(dispatch_file)
            if dispatch_df is not None:
                log.append(f"✓ Dispatch loaded: {len(dispatch_df)} entries")
            else:
                log.append("⚠ Dispatch file could not be parsed → degraded mode")
        else:
            log.append("ℹ No dispatch provided → degraded mode")

        # ── STEP 3: Merge ────────────────────────────────────────────────────
        _progress(25, "Merging dispatch and results...")
        df = merge_dispatch_and_results(
            parsed['files'],
            dispatch_df=dispatch_df,
            log=log
        )

        if df.empty:
            return {'success': False, 'error': 'Empty DataFrame after merge', 'log': log}

        # ── STEP 4: Apply conflict resolutions ───────────────────────────────
        if conflict_resolutions:
            df = _apply_resolutions(df, conflict_resolutions, log)

        # ── STEP 4b: Separate TES QC from LAB QC ─────────────────────────────
        _progress(32, "Separating TES QC from LAB QC...")
        sep_result = separate_qc(df, log=log)
        df_tes = sep_result['df_tes']
        df_lab = sep_result['df_lab']
        lab_stats = sep_result['stats']

        # ── STEP 5: Pre-analysis (returned for UI display) ────────────────────
        _progress(35, "Running pre-analysis...")
        pre = run_pre_analysis(df_tes, parsed['files'], log=log)
        pre['lab_qc_stats'] = lab_stats

        # ── STEP 6: Clean data ───────────────────────────────────────────────
        _progress(40, "Cleaning data (LOD, outliers, physical checks)...")
        df_tes, cleaning_report = clean_data(df_tes, log=log)

        # ── STEP 7: Completeness score ────────────────────────────────────────
        _progress(45, "Scoring QC completeness...")
        completeness = score_completeness(df_tes, log=log)

        # Infer zone if not provided
        if not zone:
            zone = pre.get('zones', {})
            zone = _infer_zone_from_df(df_tes)

        # ── STEP 8: QC modules (TES only - LAB QC is validated separately) ───────────
        _progress(55, "Running duplicate QC...")
        dup_results = run_duplicates_qc(df_tes, log=log)

        _progress(65, "Running blank QC...")
        blk_results = run_blanks_qc(df_tes, log=log)

        _progress(72, "Running standards QC...")
        std_results = run_standards_qc(df_tes, log=log)

        # Validation des QC Labo (si présents)
        lab_qc_results = {}
        if df_lab is not None and not df_lab.empty:
            _progress(75, "Validating Lab QC against certified values...")
            # Charger la base CRM
            import json
            try:
                with open(BASE_DIR / 'data' / 'oreas_certified.json') as f:
                    crm_db = json.load(f)
            except:
                crm_db = {}
            # Ajouter les CRMs custom si existants
            try:
                with open(BASE_DIR / 'data' / 'crm_custom.json') as f:
                    crm_db.update(json.load(f))
            except:
                pass
            
            config_path = BASE_DIR / 'config.json'
            config = {}
            try:
                with open(config_path) as f:
                    config = json.load(f)
            except:
                config = {"elements_actifs": ["Ta", "Nb", "Ti", "Rb"], "methode_defaut": "4-acides"}
            
            lab_qc_results = validate_lab_qc(df_lab, crm_db, config)
        else:
            lab_qc_results = {"status": "NO_DATA"}

        qc_results = {
            'duplicates': dup_results,
            'blanks': blk_results,
            'standards': std_results,
            'lab_qc_stats': lab_stats,  # Stats brutes sur les QC labo
            'lab_qc_validation': lab_qc_results  # Résultats de la validation
        }

        # ── STEP 9: Mode-specific modules (only A, B, D are active) ───────────
        _progress(78, f"Running Mode {mode} specific analysis...")
        mode_results = {}
        if mode == 'B':
            from qc_engine.modes.mode_b_runs import run_mode_b
            mode_results = run_mode_b(df_tes, log=log)
        elif mode == 'D':
            from qc_engine.modes.mode_d_labos import run_mode_d
            mode_results = run_mode_d(df_tes, log=log)
        # Modes C, E, F, G, H ont été retirés de l'UI car redondants ou non utilisés

        qc_results.update(mode_results)

        # ── STEP 10: Save to history ──────────────────────────────────────────
        _progress(82, "Saving batch to history...")
        batch_filename = save_batch(df_tes, qc_results, mode=mode, zone=zone)
        log.append(f"✓ Batch archived: {batch_filename}")

        # ── STEP 11: Load history for HISTORIQUE sheet ────────────────────────
        batches = list_batches(zone=zone)

        # ── STEP 12: Generate XLSX ────────────────────────────────────────────
        _progress(87, "Generating XLSX report...")
        OUTPUT_DIR.mkdir(exist_ok=True)
        xlsx_path = write_xlsx(df_tes, qc_results, mode=mode,
                                output_dir=OUTPUT_DIR, zone=zone, batches=batches,
                                df_lab=df_lab if not df_lab.empty else None)
        output_files['xlsx'] = xlsx_path
        log.append(f"✓ XLSX: {Path(xlsx_path).name}")

        # ── STEP 13: Generate PDF ─────────────────────────────────────────────
        _progress(94, "Generating PDF report...")
        pdf_path = write_pdf(df_tes, qc_results, completeness, mode=mode,
                              output_dir=OUTPUT_DIR, zone=zone,
                              df_lab=df_lab if not df_lab.empty else None)
        output_files['pdf'] = pdf_path
        log.append(f"✓ PDF: {Path(pdf_path).name}")

        # ── STEP 14: Build result summary ─────────────────────────────────────
        _progress(100, "Done!")

        overall_status = _compute_overall_status(qc_results, completeness)

        return {
            'success': True,
            'overall_status': overall_status,
            'zone': zone,
            'mode': mode,
            'batch_filename': batch_filename,
            'completeness': completeness,
            'qc_results': {
                'duplicates': _serialize_qc(dup_results),
                'blanks': _serialize_qc(blk_results),
                'standards': _serialize_qc(std_results),
            },
            'pre_analysis': pre,
            'cleaning_report': cleaning_report,
            'output_files': output_files,
            'log': log,
            'errors': errors,
            'n_records': len(df),
            'n_orig': completeness.get('n_orig', 0),
        }

    except Exception as e:
        tb = traceback.format_exc()
        log.append(f"❌ Fatal error: {e}")
        return {
            'success': False,
            'error': str(e),
            'traceback': tb,
            'log': log
        }


def run_pre_analysis_only(
    analytical_files: list[str],
    dispatch_file: str = None
) -> dict:
    """Run only parse + merge + pre-analysis. Fast. Returns UI data for Screen 3."""
    log = []
    try:
        parsed = parse_multiple_files(analytical_files)
        log.extend(parsed.get('log', []))

        dispatch_df = None
        if dispatch_file:
            dispatch_df = parse_dispatch(dispatch_file)

        df = merge_dispatch_and_results(parsed['files'], dispatch_df=dispatch_df, log=log)
        pre = run_pre_analysis(df, parsed['files'], log=log)
        pre['log'] = log
        return pre
    except Exception as e:
        return {'error': str(e), 'log': log, 'ready_to_process': False}


# ─── HELPERS ─────────────────────────────────────────────────────────────────

def _apply_resolutions(df: pd.DataFrame, resolutions: dict, log: list) -> pd.DataFrame:
    """Apply user conflict resolution choices."""
    for sid, choice in resolutions.items():
        if choice == 'duplicate_error':
            # Keep only first occurrence
            first_idx = df[df['SampleID'] == sid].index[0]
            drop_idxs = df[df['SampleID'] == sid].index[1:]
            df = df.drop(index=drop_idxs)
            log.append(f"ℹ {sid}: kept first, dropped {len(drop_idxs)} duplicates")
        elif choice == 'reanalysis':
            # Add suffix to distinguish
            rows = df[df['SampleID'] == sid]
            for i, idx in enumerate(rows.index[1:], start=2):
                df.at[idx, 'SampleID'] = f"{sid}_R{i}"
            log.append(f"ℹ {sid}: treated as re-analyses, renamed to {sid}_R2...")
        elif choice == 'flag':
            df.loc[df['SampleID'] == sid, '_conflict_flag'] = True
            log.append(f"ℹ {sid}: flagged for manual review")
    return df


def _infer_zone_from_df(df: pd.DataFrame) -> str:
    """Infer zone name from SampleIDs."""
    import re, json
    try:
        with open(BASE_DIR / 'memory.json') as f:
            mem = json.load(f)
        zones = mem.get('zones', {})
    except Exception:
        zones = {}

    sids = df['SampleID'].astype(str) if 'SampleID' in df.columns else pd.Series([])
    nums = []
    for sid in sids:
        m = re.search(r'(\d+)', sid)
        if m:
            nums.append(int(m.group(1)))

    if nums:
        median_num = sorted(nums)[len(nums) // 2]
        for zone, bounds in zones.items():
            if bounds.get('sm_min', 0) <= median_num <= bounds.get('sm_max', 999999):
                return zone

    return 'UNKNOWN'


def _compute_overall_status(qc_results: dict, completeness: dict) -> str:
    """Compute overall batch status."""
    issues = []
    dup = qc_results.get('duplicates', {})
    if dup.get('n_fail', 0) > 0:
        issues.append('FAIL')
    elif dup.get('n_warn', 0) > 0:
        issues.append('WARN')

    for crm_res in qc_results.get('blanks', {}).get('crm_results', {}).values():
        issues.append(crm_res.get('overall_status', 'PASS'))
    for crm_res in qc_results.get('standards', {}).get('crm_results', {}).values():
        issues.append(crm_res.get('overall_status', 'PASS'))

    if 'FAIL' in issues:
        return 'FAIL'
    if 'WARN' in issues:
        return 'WARN'
    return 'PASS'


def _serialize_qc(obj):
    """Make QC results JSON-serializable."""
    import numpy as np
    if isinstance(obj, dict):
        return {k: _serialize_qc(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_serialize_qc(i) for i in obj]
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating,)):
        return float(obj)
    if isinstance(obj, np.ndarray):
        return obj.tolist()
    return obj
