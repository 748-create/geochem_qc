"""
cleaner.py — Data cleaning: LOD handling, outlier detection, physical error checks.
"""

import pandas as pd
import numpy as np
import json
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent.parent
CONFIG_PATH = BASE_DIR / "config.json"


def load_config() -> dict:
    with open(CONFIG_PATH) as f:
        return json.load(f)


def get_element_cols(df: pd.DataFrame) -> list[str]:
    """Return list of _ppm column names present in df."""
    return [c for c in df.columns if c.endswith('_ppm')]


def clean_data(df: pd.DataFrame, log: list = None) -> tuple[pd.DataFrame, dict]:
    """
    Full cleaning pipeline.
    Returns (cleaned_df, cleaning_report)
    """
    if log is None:
        log = []

    cfg = load_config()
    seuils = cfg.get('seuils', {})
    iqr_mult = seuils.get('outlier_iqr', 1.5)
    z_thresh = seuils.get('outlier_zscore', 3)

    report = {
        'lod_replacements': {},
        'outliers': [],
        'physical_errors': [],
        'flags': {}
    }

    elem_cols = get_element_cols(df)

    # Only clean ORIG samples
    orig_mask = df['SampleType'] == 'ORIG'

    # ── 1. LOD handling ──
    # Values = 0.0 means <LOD → replace with LOD/2
    # We keep a flag column for each element
    for col in elem_cols:
        el = col.replace('_ppm', '')
        lod_mask = (df[col] == 0.0) & orig_mask
        n_lod = lod_mask.sum()
        if n_lod > 0:
            # Estimate LOD from non-zero values (use 10th percentile as proxy)
            valid = df.loc[orig_mask & (df[col] > 0), col]
            if len(valid) > 0:
                lod_estimate = valid.quantile(0.05)
                df.loc[lod_mask, col] = lod_estimate / 2
                report['lod_replacements'][el] = {'n': int(n_lod), 'lod_est': float(lod_estimate)}
            # If all are LOD, keep 0
        # Flag column
        df[f'{el}_LOD_flag'] = lod_mask.astype(int)

    if report['lod_replacements']:
        parts = [f"{k}({v['n']})" for k, v in report['lod_replacements'].items()]
        log.append("ℹ LOD/ND replacements: " + ", ".join(parts))

    # ── 2. Physical error checks ──
    # Check for physically impossible values
    physical_limits = {
        'Ta': 1e6, 'Nb': 1e6, 'Ti': 1e6, 'Li': 1e6,
        'Fe': 1e6, 'Al': 1e6, 'Si': 1e6, 'Ca': 1e6,
        'Cu': 5e5, 'Pb': 5e5, 'Zn': 5e5,
    }

    for col in elem_cols:
        el = col.replace('_ppm', '')
        limit = physical_limits.get(el, 1e7)
        bad_mask = (df[col] > limit) & orig_mask
        if bad_mask.sum() > 0:
            bad_sids = df.loc[bad_mask, 'SampleID'].tolist()
            report['physical_errors'].append({
                'element': el, 'samples': bad_sids, 'limit': limit
            })
            df.loc[bad_mask, col] = np.nan
            log.append(f"⚠ Physical error {el}: {len(bad_sids)} values > {limit} ppm → NaN")

    # ── 3. Outlier detection ──
    # IQR method on ORIG samples per element
    outlier_flags = {}
    for col in elem_cols:
        el = col.replace('_ppm', '')
        orig_vals = df.loc[orig_mask & df[col].notna(), col]
        if len(orig_vals) < 4:
            continue

        q1 = orig_vals.quantile(0.25)
        q3 = orig_vals.quantile(0.75)
        iqr = q3 - q1

        if iqr == 0:
            continue

        lower = q1 - iqr_mult * iqr
        upper = q3 + iqr_mult * iqr

        # Z-score
        mean = orig_vals.mean()
        std = orig_vals.std()

        outlier_mask = orig_mask & (
            (df[col] < lower) | (df[col] > upper)
        )
        if std > 0:
            z_mask = orig_mask & ((df[col] - mean).abs() / std > z_thresh)
            outlier_mask = outlier_mask & z_mask  # Both IQR and Z must flag

        n_out = outlier_mask.sum()
        if n_out > 0:
            outlier_flags[el] = df.loc[outlier_mask, 'SampleID'].tolist()
            df.loc[outlier_mask, f'{el}_outlier_flag'] = 1
            report['outliers'].append({
                'element': el, 'n': int(n_out),
                'samples': outlier_flags[el][:5],
                'bounds': [float(lower), float(upper)]
            })

    if report['outliers']:
        total_out = sum(o['n'] for o in report['outliers'])
        log.append(f"ℹ Outliers flagged: {total_out} total across {len(report['outliers'])} elements (not removed, only flagged)")

    # ── 4. Build flag summary per sample ──
    for idx, row in df.iterrows():
        flags = []
        for col in elem_cols:
            el = col.replace('_ppm', '')
            if df.at[idx, f'{el}_LOD_flag'] if f'{el}_LOD_flag' in df.columns else False:
                flags.append(f'{el}<LOD')
            if df.at[idx, f'{el}_outlier_flag'] if f'{el}_outlier_flag' in df.columns else False:
                flags.append(f'{el}_OUTLIER')
        if flags:
            report['flags'][str(row['SampleID'])] = flags

    log.append(f"✓ Cleaning complete")
    return df, report
