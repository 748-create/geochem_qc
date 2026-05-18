"""
completeness.py — QC completeness scoring: COMPLET / PARTIEL / MINIMAL / ASYMÉTRIQUE
"""

import pandas as pd
import numpy as np
import json
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent.parent
CONFIG_PATH = BASE_DIR / "config.json"


def load_config():
    with open(CONFIG_PATH) as f:
        return json.load(f)


def score_completeness(df: pd.DataFrame, log: list = None) -> dict:
    if log is None:
        log = []

    cfg = load_config()

    orig = df[df['SampleType'] == 'ORIG']
    dup = df[(df['SampleType'] == 'QAQC') & (df['QAQCType'] == 'DUP') & (df['QC_Source'] == 'TES')]
    blk = df[(df['SampleType'] == 'QAQC') & (df['QAQCType'] == 'BLK') & (df['QC_Source'] == 'TES')]
    std = df[(df['SampleType'] == 'QAQC') & (df['QAQCType'] == 'STD') & (df['QC_Source'] == 'TES')]
    lab_qc = df[df.get('QC_Source', pd.Series(['TES'] * len(df))) == 'LAB'] if 'QC_Source' in df.columns else pd.DataFrame()

    n_orig = len(orig)
    n_dup = len(dup)
    n_blk = len(blk)
    n_std = len(std)

    # Industry standards: 1 DUP per 20 samples, 1 BLK per 20, 1 STD per 20
    target_rate = 0.05  # 5% = 1 per 20
    dup_rate = n_dup / n_orig if n_orig > 0 else 0
    blk_rate = n_blk / n_orig if n_orig > 0 else 0
    std_rate = n_std / n_orig if n_orig > 0 else 0

    has_dup = n_dup > 0
    has_blk = n_blk > 0
    has_std = n_std > 0

    # Score
    score = 'COMPLET'
    issues = []

    if not has_dup and not has_blk and not has_std:
        score = 'MINIMAL'
        issues.append('No QC samples (DUP/BLK/STD) found')
    elif not has_dup:
        issues.append('No duplicates')
        score = 'PARTIEL'
    elif not has_blk:
        issues.append('No blanks')
        score = 'PARTIEL'
    elif not has_std:
        issues.append('No standards')
        score = 'PARTIEL'

    # Rate checks
    if has_dup and dup_rate < target_rate * 0.5:
        issues.append(f'Low DUP rate: {dup_rate:.1%} (target ≥{target_rate:.0%})')
        if score == 'COMPLET':
            score = 'PARTIEL'

    # Asymmetry check: lots of one type, none of another
    qc_types_present = sum([has_dup, has_blk, has_std])
    if qc_types_present == 2 and (n_dup > 5 or n_std > 5):
        score = 'ASYMÉTRIQUE'

    # Mass completeness
    mass_cols = [c for c in df.columns if 'Mass' in c and 'kg' in c.lower()]
    mass_completeness = None
    if mass_cols and n_orig > 0:
        col = mass_cols[0]
        n_with_mass = orig[col].notna().sum()
        mass_completeness = {
            'n_with_mass': int(n_with_mass),
            'n_total': n_orig,
            'pct': round(n_with_mass / n_orig * 100, 1)
        }
        missing_mass = orig[orig[col].isna()]['SampleID'].tolist()[:10]
        if missing_mass:
            issues.append(f'{n_orig - n_with_mass} samples missing mass')

    log.append(f"✓ QC Score: {score} | {n_orig} ORIG / {n_dup} DUP / {n_blk} BLK / {n_std} STD")

    return {
        'score': score,
        'n_orig': n_orig,
        'n_dup': n_dup,
        'n_blk': n_blk,
        'n_std': n_std,
        'n_lab_qc': len(lab_qc),
        'dup_rate': round(dup_rate * 100, 2),
        'blk_rate': round(blk_rate * 100, 2),
        'std_rate': round(std_rate * 100, 2),
        'issues': issues,
        'mass_completeness': mass_completeness,
    }
