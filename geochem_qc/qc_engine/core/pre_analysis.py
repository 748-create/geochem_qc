"""
pre_analysis.py — Pre-analysis: summarize what was understood before full processing.
Returns a dict that the UI shows at Screen 3.
"""

import pandas as pd
import numpy as np
import json
import re
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent.parent
MEMORY_PATH = BASE_DIR / "memory.json"


def load_memory():
    try:
        with open(MEMORY_PATH) as f:
            return json.load(f)
    except Exception:
        return {}


def run_pre_analysis(df: pd.DataFrame, parsed_files: list[dict], log: list = None) -> dict:
    """
    Summarize parsed data before QC processing.
    Returns UI-ready summary with flags and one question at a time.
    """
    if log is None:
        log = []

    mem = load_memory()

    # Counts
    orig = df[df['SampleType'] == 'ORIG']
    dup = df[(df['SampleType'] == 'QAQC') & (df['QAQCType'] == 'DUP') & (df.get('QC_Source', pd.Series(['TES'] * len(df))) == 'TES')]
    blk = df[(df['SampleType'] == 'QAQC') & (df['QAQCType'] == 'BLK') & (df.get('QC_Source', pd.Series(['TES'] * len(df))) == 'TES')]
    std = df[(df['SampleType'] == 'QAQC') & (df['QAQCType'] == 'STD') & (df.get('QC_Source', pd.Series(['TES'] * len(df))) == 'TES')]
    lab_qc = df[df.get('QC_Source', pd.Series(['TES'] * len(df))) == 'LAB'] if 'QC_Source' in df.columns else pd.DataFrame()

    # CRM names found
    blk_crms = blk['IDBlk'].dropna().unique().tolist() if 'IDBlk' in blk.columns else []
    std_crms = std['IDStd'].dropna().unique().tolist() if 'IDStd' in std.columns else []

    # Formats detected
    formats_detected = list(set(f.get('format', 'unknown') for f in parsed_files))
    methods_detected = list(set(f.get('method_type', 'unknown') for f in parsed_files))
    instruments_detected = list(set(f.get('instrument', 'unknown') for f in parsed_files))

    # Elements found
    elem_cols = [c.replace('_ppm', '') for c in df.columns
                 if c.endswith('_ppm') and not any(x in c for x in ['LOD', 'outlier', 'flag'])]

    # Mass completeness
    mass_col = next((c for c in df.columns if 'mass' in c.lower() and 'bulk' in c.lower()), None)
    mass_info = None
    if mass_col and len(orig) > 0:
        n_with_mass = orig[mass_col].notna().sum()
        missing_sids = orig[orig[mass_col].isna()]['SampleID'].tolist()[:5]
        mass_info = {
            'n_with': int(n_with_mass),
            'n_total': len(orig),
            'missing_examples': missing_sids
        }

    # Detect conflicts: same SampleID with different values
    conflicts = []
    if 'SampleID' in df.columns:
        dup_sids = df[df.duplicated('SampleID', keep=False)]['SampleID'].unique()
        for sid in dup_sids[:5]:  # limit to 5
            rows = df[df['SampleID'] == sid]
            conflict_vals = {}
            for col in [c for c in df.columns if c.endswith('_ppm')][:4]:
                vals = rows[col].dropna().unique()
                if len(vals) > 1:
                    conflict_vals[col.replace('_ppm', '')] = [round(float(v), 2) for v in vals[:2]]
            if conflict_vals:
                conflicts.append({
                    'sample_id': sid,
                    'values': conflict_vals,
                    'question': f'SampleID {sid} appears {len(rows)}x with different values',
                    'options': [
                        {'id': 'duplicate_error', 'label': 'Duplicate entry error — keep first'},
                        {'id': 'reanalysis', 'label': 'Re-analysis — keep both'},
                        {'id': 'flag', 'label': 'Flag for manual review'}
                    ]
                })

    # Unmatched lab QC
    lab_qc_ids = lab_qc['SampleID'].tolist()[:5] if not lab_qc.empty else []

    # Missing parent IDs for dups
    orphan_dups = []
    if not dup.empty and 'IDParent' in dup.columns:
        no_parent = dup[dup['IDParent'].isna() | (dup['IDParent'].astype(str) == 'nan')]
        orphan_dups = no_parent['SampleID'].tolist()

    # Build confirmed items list (✓)
    confirmed = []
    confirmed.append(f"{len(orig)} ORIG / {len(blk)} BLK / {len(std)} STD / {len(dup)} DUP")
    for crm in blk_crms:
        confirmed.append(f"{crm} → {int(blk['IDBlk'].eq(crm).sum() if 'IDBlk' in blk.columns else 0)} blanks")
    for crm in std_crms:
        confirmed.append(f"{crm} → {int(std['IDStd'].eq(crm).sum() if 'IDStd' in std.columns else 0)} standards")
    for fmt in formats_detected:
        confirmed.append(f"Format: {fmt}")
    for m in methods_detected:
        confirmed.append(f"Method type: {m}")
    if elem_cols:
        confirmed.append(f"{len(elem_cols)} elements: {', '.join(elem_cols[:8])}{'...' if len(elem_cols) > 8 else ''}")

    # Warnings (⚠)
    warnings = []
    if mass_info and mass_info['n_with'] < mass_info['n_total']:
        n_miss = mass_info['n_total'] - mass_info['n_with']
        warnings.append(f"{n_miss} samples missing mass ({', '.join(mass_info['missing_examples'][:3])}{'...' if n_miss > 3 else ''})")
    if orphan_dups:
        warnings.append(f"{len(orphan_dups)} DUP without parent ID → will attempt inference")
    if lab_qc_ids:
        warnings.append(f"{len(lab_qc_ids)} lab QC IDs outside dispatch → treated as informational")
    for f in parsed_files:
        if f.get('n_records', 0) == 0:
            warnings.append(f"File {f.get('source_file','?')} returned 0 records")

    # One question at a time
    pending_question = conflicts[0] if conflicts else None

    return {
        'confirmed': confirmed,
        'warnings': warnings,
        'pending_question': pending_question,
        'all_conflicts': conflicts,
        'n_orig': len(orig),
        'n_dup': len(dup),
        'n_blk': len(blk),
        'n_std': len(std),
        'n_lab_qc': len(lab_qc),
        'elements': elem_cols,
        'formats': formats_detected,
        'methods': methods_detected,
        'instruments': instruments_detected,
        'mass_info': mass_info,
        'ready_to_process': True
    }
