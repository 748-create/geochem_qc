"""
historique.py — Batch history: save/load JSON archives per zone/month/batch.
"""

import json
import os
import re
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent.parent
HIST_DIR = BASE_DIR / "historique"
MEMORY_PATH = BASE_DIR / "memory.json"


def _infer_zone(df) -> str:
    """Infer zone from SampleID prefix or memory zones."""
    try:
        with open(MEMORY_PATH) as f:
            mem = json.load(f)
        zones = mem.get('zones', {})
    except Exception:
        zones = {}

    if 'SampleID' not in df.columns:
        return 'UNKNOWN'

    # Try numeric SM IDs
    sids = df['SampleID'].astype(str)
    nums = []
    for sid in sids:
        m = re.search(r'(\d+)', sid)
        if m:
            nums.append(int(m.group(1)))
    if not nums:
        return 'UNKNOWN'

    median_num = sorted(nums)[len(nums) // 2]
    for zone, bounds in zones.items():
        if bounds['sm_min'] <= median_num <= bounds['sm_max']:
            return zone

    # Try prefix
    prefixes = sids.str.extract(r'^([A-Z]+)', expand=False).dropna()
    if not prefixes.empty:
        return prefixes.mode()[0]

    return 'UNKNOWN'


def _next_batch_number(zone: str, yyyymm: str) -> str:
    """Find next available batch number NNN for zone/month."""
    HIST_DIR.mkdir(exist_ok=True)
    existing = list(HIST_DIR.glob(f"{zone}-{yyyymm}-*.json"))
    if not existing:
        return "001"
    nums = []
    for p in existing:
        m = re.search(r'-(\d{3})\.json$', p.name)
        if m:
            nums.append(int(m.group(1)))
    return f"{max(nums) + 1:03d}"


def save_batch(df, qc_results: dict, mode: str, zone: str = None) -> str:
    """
    Save batch to historique/[Zone]-[YYYYMM]-[NNN].json.
    Returns filename.
    """
    HIST_DIR.mkdir(exist_ok=True)
    now = datetime.now()
    yyyymm = now.strftime('%Y%m')

    if not zone:
        zone = _infer_zone(df)

    batch_num = _next_batch_number(zone, yyyymm)
    filename = f"{zone}-{yyyymm}-{batch_num}.json"
    filepath = HIST_DIR / filename

    # Build archive
    archive = {
        'filename': filename,
        'zone': zone,
        'date': now.isoformat(),
        'mode': mode,
        'n_orig': int((df['SampleType'] == 'ORIG').sum()),
        'n_dup': int(((df['SampleType'] == 'QAQC') & (df['QAQCType'] == 'DUP')).sum()),
        'n_blk': int(((df['SampleType'] == 'QAQC') & (df['QAQCType'] == 'BLK')).sum()),
        'n_std': int(((df['SampleType'] == 'QAQC') & (df['QAQCType'] == 'STD')).sum()),
        'elements': [c.replace('_ppm', '') for c in df.columns if c.endswith('_ppm')
                     and not any(x in c for x in ['LOD', 'outlier'])],
        'sample_ids': df['SampleID'].tolist(),
        'qc_summary': {
            'dup_n_fail': qc_results.get('duplicates', {}).get('n_fail', 0),
            'dup_n_warn': qc_results.get('duplicates', {}).get('n_warn', 0),
            'blk_status': {k: v.get('overall_status') for k, v in
                           qc_results.get('blanks', {}).get('crm_results', {}).items()},
            'std_status': {k: v.get('overall_status') for k, v in
                           qc_results.get('standards', {}).get('crm_results', {}).items()},
        },
        'data': df.to_dict(orient='records')
    }

    with open(filepath, 'w', encoding='utf-8') as f:
        json.dump(archive, f, indent=2, ensure_ascii=False, default=str)

    return filename


def load_batch(filename: str) -> dict | None:
    """Load a batch archive by filename."""
    filepath = HIST_DIR / filename
    if not filepath.exists():
        return None
    with open(filepath, encoding='utf-8') as f:
        return json.load(f)


def list_batches(zone: str = None) -> list[dict]:
    """List all archived batches, optionally filtered by zone."""
    HIST_DIR.mkdir(exist_ok=True)
    batches = []
    for p in sorted(HIST_DIR.glob("*.json")):
        try:
            with open(p) as f:
                meta = json.load(f)
            if zone and meta.get('zone') != zone:
                continue
            batches.append({
                'filename': p.name,
                'zone': meta.get('zone'),
                'date': meta.get('date'),
                'mode': meta.get('mode'),
                'n_orig': meta.get('n_orig'),
                'n_dup': meta.get('n_dup'),
                'n_blk': meta.get('n_blk'),
                'n_std': meta.get('n_std'),
            })
        except Exception:
            continue
    return batches
