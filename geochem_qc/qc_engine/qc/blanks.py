"""
blanks.py — Blank QC: Shewhart control charts, bias/drift detection, contamination flags.
"""

import pandas as pd
import numpy as np
import json
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent.parent
CONFIG_PATH = BASE_DIR / "config.json"
OREAS_PATH = BASE_DIR / "data" / "oreas_certified.json"
CUSTOM_CRM_PATH = BASE_DIR / "data" / "crm_custom.json"


def load_config():
    with open(CONFIG_PATH) as f:
        return json.load(f)


def load_certified(crm_id: str, method: str) -> dict:
    """Load certified values for a CRM and method."""
    for path in [OREAS_PATH, CUSTOM_CRM_PATH]:
        try:
            with open(path) as f:
                db = json.load(f)
            if crm_id in db:
                methods = db[crm_id].get('methods', {})
                # Try exact method, then similar
                if method in methods:
                    return methods[method]
                # Fallback: try pXRF or first available
                for m in ['pXRF', 'ME-MS89L', 'IMS-230', 'fusion_peroxyde']:
                    if m in methods:
                        return methods[m]
        except Exception:
            continue
    return {}


def run_blanks_qc(df: pd.DataFrame, log: list = None) -> dict:
    """
    Run blank QC (Shewhart).
    For Ta/Nb projects: OREAS20a / OREAS22e used as blanks (barren granodiorite).
    Checks: contamination (value > 3SD above background), bias, drift.
    """
    if log is None:
        log = []

    cfg = load_config()
    seuils = cfg['seuils']
    warn_sd = seuils.get('blk_warn_sd', 2)
    fail_sd = seuils.get('blk_fail_sd', 3)
    drift_points = seuils.get('drift_points', 3)
    elements_prioritaires = cfg.get('elements_prioritaires', ['Ta', 'Nb', 'Ti', 'Rb'])
    elements_actifs = cfg.get('elements_actifs', [])

    # TES blanks only
    blk_mask = (df['SampleType'] == 'QAQC') & (df['QAQCType'] == 'BLK') & (df['QC_Source'] == 'TES')
    blk_df = df[blk_mask].copy().reset_index(drop=True)

    if blk_df.empty:
        log.append("ℹ No TES blanks found")
        return {'crm_results': {}, 'n_blanks': 0}

    # Group by CRM name
    crm_groups = {}
    for _, row in blk_df.iterrows():
        crm_id = row.get('IDBlk') or row.get('SampleID', 'UNKNOWN')
        # Normalize CRM name
        crm_id = _normalize_crm_name(crm_id)
        crm_groups.setdefault(crm_id, []).append(row)

    all_results = {}
    active_elements = [e for e in elements_actifs + elements_prioritaires
                       if f'{e}_ppm' in df.columns]

    for crm_id, rows in crm_groups.items():
        crm_df = pd.DataFrame(rows)
        method = crm_df.iloc[0].get('Method', 'pXRF')
        certified = load_certified(crm_id, method)

        crm_result = {
            'crm_id': crm_id,
            'n_measurements': len(crm_df),
            'method': method,
            'elements': {},
            'overall_status': 'PASS',
            'shewhart_data': {}
        }

        for el in active_elements:
            col = f'{el}_ppm'
            if col not in crm_df.columns:
                continue

            vals = crm_df[col].dropna().values
            if len(vals) == 0:
                continue

            cert = certified.get(el, {})
            cert_val = cert.get('certified')
            cert_sd = cert.get('1SD')

            # Background check (for blank CRMs, certified should be very low)
            # If no certified value → use mean + Nσ approach
            mean_measured = float(np.mean(vals))
            std_measured = float(np.std(vals)) if len(vals) > 1 else 0.0

            el_result = {
                'n': len(vals),
                'mean': round(mean_measured, 4),
                'std': round(std_measured, 4),
                'values': [round(float(v), 4) for v in vals],
                'status': 'PASS',
                'issues': []
            }

            if cert_val is not None and cert_val > 0:
                # Contamination check: measured >> certified
                upper_warn = cert_val + warn_sd * (cert_sd or cert_val * 0.1)
                upper_fail = cert_val + fail_sd * (cert_sd or cert_val * 0.1)

                n_fail_cont = sum(1 for v in vals if v > upper_fail)
                n_warn_cont = sum(1 for v in vals if upper_warn < v <= upper_fail)

                if n_fail_cont > 0:
                    el_result['status'] = 'FAIL'
                    el_result['issues'].append(f'CONTAMINATION: {n_fail_cont} values > {fail_sd}SD above certified')
                    crm_result['overall_status'] = 'FAIL'
                elif n_warn_cont > 0:
                    el_result['status'] = 'WARN'
                    el_result['issues'].append(f'WARN: {n_warn_cont} values > {warn_sd}SD above certified')
                    if crm_result['overall_status'] == 'PASS':
                        crm_result['overall_status'] = 'WARN'

                el_result['certified'] = round(cert_val, 4)
                el_result['certified_sd'] = round(cert_sd, 4) if cert_sd else None

            elif cert_val == 0 or cert_val is None:
                # No certified value → use 3× measured mean as contamination threshold
                threshold = max(mean_measured * 3, std_measured * fail_sd, 1.0)
                n_fail_cont = sum(1 for v in vals if v > threshold)
                if n_fail_cont > 0:
                    el_result['status'] = 'WARN'
                    el_result['issues'].append(f'Elevated background (>{round(threshold,1)} ppm) in {n_fail_cont} measurements')

            # Drift check: are values trending up over sequence?
            if len(vals) >= drift_points:
                x = np.arange(len(vals), dtype=float)
                slope = np.polyfit(x, vals, 1)[0]
                # Flag if slope > 10% of mean per measurement
                if mean_measured > 0:
                    slope_pct = abs(slope) / mean_measured * 100
                    if slope_pct > 10:
                        direction = '↗' if slope > 0 else '↘'
                        el_result['drift'] = {
                            'slope': round(float(slope), 4),
                            'direction': direction,
                            'slope_pct_per_measure': round(slope_pct, 2)
                        }
                        el_result['issues'].append(f'DRIFT {direction}: {round(slope_pct,1)}%/measurement')
                        if el in elements_prioritaires and el_result['status'] == 'PASS':
                            el_result['status'] = 'WARN'

            # Shewhart data for chart
            if cert_val is not None and cert_val > 0:
                crm_result['shewhart_data'][el] = {
                    'values': el_result['values'],
                    'mean': el_result['mean'],
                    'ucl': round(cert_val + fail_sd * (cert_sd or cert_val * 0.1), 4),
                    'uwl': round(cert_val + warn_sd * (cert_sd or cert_val * 0.1), 4),
                    'target': round(cert_val, 4),
                    'lwl': max(0, round(cert_val - warn_sd * (cert_sd or cert_val * 0.1), 4)),
                    'lcl': max(0, round(cert_val - fail_sd * (cert_sd or cert_val * 0.1), 4)),
                }

            crm_result['elements'][el] = el_result

        all_results[crm_id] = crm_result

    n_pass = sum(1 for r in all_results.values() if r['overall_status'] == 'PASS')
    n_warn = sum(1 for r in all_results.values() if r['overall_status'] == 'WARN')
    n_fail = sum(1 for r in all_results.values() if r['overall_status'] == 'FAIL')

    log.append(f"✓ BLK QC: {len(all_results)} CRMs — {n_pass} PASS / {n_warn} WARN / {n_fail} FAIL")

    return {
        'crm_results': all_results,
        'n_blanks': len(blk_df),
        'n_crms': len(all_results),
        'n_pass': n_pass, 'n_warn': n_warn, 'n_fail': n_fail
    }


def _normalize_crm_name(raw: str) -> str:
    """Normalize CRM name for lookup."""
    import re
    s = str(raw).strip().upper()
    # OREAS 20a → OREAS20a
    s = re.sub(r'\s+', '', s)
    # Map variants
    aliases = {
        'OREAS20A': 'OREAS20a', 'OR20A': 'OREAS20a',
        'OREAS22E': 'OREAS22e', 'OR22E': 'OREAS22e',
        'OREAS750B': 'OREAS750b', 'OREAS751B': 'OREAS751b',
        'OREAS752B': 'OREAS752b', 'OREAS753B': 'OREAS753b',
        'OREAS754': 'OREAS754', 'OREAS999B': 'OREAS999b',
        'OREAS601': 'OREAS601',
    }
    return aliases.get(s, s)
