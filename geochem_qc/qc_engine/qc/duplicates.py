"""
duplicates.py — Duplicate QC: RPD calculation, pairing, regression.
"""

import pandas as pd
import numpy as np
from scipy import stats
import json
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent.parent
CONFIG_PATH = BASE_DIR / "config.json"


def load_config() -> dict:
    with open(CONFIG_PATH) as f:
        return json.load(f)


def calc_rpd(a: float, b: float) -> float | None:
    """Relative Percent Difference."""
    if a is None or b is None or np.isnan(a) or np.isnan(b):
        return None
    denom = (abs(a) + abs(b)) / 2
    if denom == 0:
        return 0.0
    return abs(a - b) / denom * 100


def run_duplicates_qc(df: pd.DataFrame, log: list = None) -> dict:
    """
    Process duplicate pairs and compute RPD for all active elements.
    Returns: {pairs: [...], summary: {...}, charts_data: [...]}
    """
    if log is None:
        log = []

    cfg = load_config()
    seuils = cfg['seuils']
    rpd_warn = seuils.get('rpd_warn', 10)
    rpd_fail = seuils.get('rpd_fail', 20)
    elements_prioritaires = cfg.get('elements_prioritaires', ['Ta', 'Nb', 'Ti', 'Rb'])
    elements_actifs = cfg.get('elements_actifs', [])

    # Get duplicate rows
    dup_mask = (df['SampleType'] == 'QAQC') & (df['QAQCType'] == 'DUP') & (df['QC_Source'] == 'TES')
    dup_df = df[dup_mask].copy()

    if dup_df.empty:
        log.append("ℹ No TES duplicates found")
        return {'pairs': [], 'summary': {}, 'n_pairs': 0}

    # Get element columns
    elem_cols = [c for c in df.columns if c.endswith('_ppm') and not any(
        x in c for x in ['_LOD', '_outlier', '_flag'])]
    active_elem_cols = []
    for col in elem_cols:
        el = col.replace('_ppm', '')
        if el in elements_actifs or el in elements_prioritaires:
            active_elem_cols.append(col)

    pairs = []
    all_rpd = {el: [] for el in [c.replace('_ppm', '') for c in active_elem_cols]}

    for _, dup_row in dup_df.iterrows():
        sid_dup = dup_row['SampleID']
        sid_parent = dup_row.get('IDParent')

        # Find parent
        if sid_parent and str(sid_parent) not in ('nan', 'None', ''):
            parent_rows = df[df['SampleID'] == str(sid_parent)]
        else:
            # Heuristic: find similar SM number
            parent_rows = pd.DataFrame()
            log.append(f"⚠ DUP {sid_dup}: no parent ID → trying to infer")

        if parent_rows.empty:
            # Try to find by proximity in sequence
            log.append(f"⚠ DUP {sid_dup}: parent not found → pair skipped")
            pairs.append({
                'dup_id': sid_dup,
                'parent_id': str(sid_parent) if sid_parent else 'UNKNOWN',
                'status': 'NO_PARENT',
                'rpd_values': {}
            })
            continue

        parent_row = parent_rows.iloc[0]
        pair = {
            'dup_id': sid_dup,
            'parent_id': parent_row['SampleID'],
            'rpd_values': {},
            'overall_status': 'PASS',
        }

        for col in active_elem_cols:
            el = col.replace('_ppm', '')
            v_orig = parent_row.get(col)
            v_dup = dup_row.get(col)

            if pd.isna(v_orig) or pd.isna(v_dup):
                continue

            rpd = calc_rpd(float(v_orig), float(v_dup))
            if rpd is None:
                continue

            status = 'PASS'
            if rpd >= rpd_fail:
                status = 'FAIL'
                pair['overall_status'] = 'FAIL'
            elif rpd >= rpd_warn:
                status = 'WARN'
                if pair['overall_status'] == 'PASS':
                    pair['overall_status'] = 'WARN'

            pair['rpd_values'][el] = {
                'orig': float(v_orig),
                'dup': float(v_dup),
                'rpd': round(rpd, 2),
                'status': status
            }
            all_rpd[el].append(rpd)

        pairs.append(pair)

    # Summary stats
    summary = {}
    for el, rpd_list in all_rpd.items():
        if not rpd_list:
            continue
        arr = np.array(rpd_list)
        summary[el] = {
            'n': len(arr),
            'mean_rpd': float(round(arr.mean(), 2)),
            'max_rpd': float(round(arr.max(), 2)),
            'n_fail': int((arr >= rpd_fail).sum()),
            'n_warn': int(((arr >= rpd_warn) & (arr < rpd_fail)).sum()),
            'n_pass': int((arr < rpd_warn).sum()),
        }

    n_fail = sum(1 for p in pairs if p.get('overall_status') == 'FAIL')
    n_warn = sum(1 for p in pairs if p.get('overall_status') == 'WARN')
    n_pass = sum(1 for p in pairs if p.get('overall_status') == 'PASS')

    log.append(f"✓ DUP QC: {len(pairs)} pairs — {n_pass} PASS / {n_warn} WARN / {n_fail} FAIL")

    # Regression data for priority elements (scatter orig vs dup)
    charts_data = []
    orig_df = df[df['SampleType'] == 'ORIG']
    for el in elements_prioritaires:
        col = f'{el}_ppm'
        if col not in df.columns:
            continue
        x_vals = []
        y_vals = []
        for p in pairs:
            rv = p.get('rpd_values', {}).get(el)
            if rv:
                x_vals.append(rv['orig'])
                y_vals.append(rv['dup'])

        if len(x_vals) < 2:
            continue

        # Linear regression
        slope, intercept, r_value, p_value, se = stats.linregress(x_vals, y_vals)
        charts_data.append({
            'element': el,
            'x': x_vals,
            'y': y_vals,
            'slope': round(slope, 4),
            'intercept': round(intercept, 4),
            'r2': round(r_value**2, 4),
            'labels': [p['dup_id'] for p in pairs if el in p.get('rpd_values', {})]
        })

    return {
        'pairs': pairs,
        'summary': summary,
        'charts_data': charts_data,
        'n_pairs': len(pairs),
        'n_fail': n_fail,
        'n_warn': n_warn,
        'n_pass': n_pass,
        'thresholds': {'warn': rpd_warn, 'fail': rpd_fail}
    }
