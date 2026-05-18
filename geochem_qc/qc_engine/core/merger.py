"""
merger.py — Fuse dispatch with analytical results.
Handles: Vanta VMR (integrated dispatch), explicit dispatch file, or dispatch-less mode.
"""

import pandas as pd
import numpy as np
import re
import json
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent.parent
MEMORY_PATH = BASE_DIR / "memory.json"

# ─── DISPATCH PARSER ─────────────────────────────────────────────────────────

DISPATCH_COLS = {
    'SampleID': ['sampleid', 'sample id', 'sample_id', 'sm', 'id'],
    'PlannedID': ['plannedid', 'planned id', 'planned_id'],
    'HoleID': ['holeid', 'hole id', 'hole_id', 'forageID'],
    'SiteID': ['siteid', 'site id', 'site_id'],
    'ActivityType': ['activitytype', 'activity type', 'activity_type', 'type', 'activité'],
    'From_m': ['from_m', 'from', 'début', 'de', 'depth from', 'depthfrom'],
    'To_m': ['to_m', 'to', 'fin', 'à', 'depth to', 'depthto'],
    'Mass_Bulk_kg': ['mass_bulk_kg', 'mass bulk', 'massbulk', 'bulk mass', 'masse bulk'],
    'Mass_HMC_g': ['mass_hmc_g', 'mass hmc', 'masshmc', 'hmc mass', 'masse hmc'],
    'SampleType': ['sampletype', 'sample type', 'sample_type', 'type'],
    'QAQCType': ['qaqqctype', 'qaqc type', 'qaqc_type', 'qctype'],
    'IDParent': ['idparent', 'parent id', 'parent_id', 'id parent'],
    'IDBlk': ['idblk', 'blk id', 'blank id', 'id blk', 'id blank'],
    'IDStd': ['idstd', 'std id', 'standard id', 'id std'],
    'Commentaire': ['commentaire', 'comment', 'notes', 'note'],
}


def _normalize_col(c: str) -> str:
    return re.sub(r'[^a-z0-9]', '', str(c).lower())


def parse_dispatch(filepath: str) -> pd.DataFrame | None:
    """Parse a dispatch file (Excel or CSV) into a standardized DataFrame."""
    path = Path(filepath)
    ext = path.suffix.lower()

    try:
        if ext in ('.xlsx', '.xlsm'):
            df = pd.read_excel(filepath)
        elif ext == '.xls':
            df = pd.read_excel(filepath, engine='xlrd')
        elif ext in ('.csv', '.txt'):
            # Try semicolon then comma
            for sep in [';', ',', '\t']:
                try:
                    df = pd.read_csv(filepath, sep=sep)
                    if df.shape[1] > 2:
                        break
                except Exception:
                    continue
        else:
            return None
    except Exception as e:
        return None

    if df is None or df.empty:
        return None

    # Map columns to standard names
    col_norm = {_normalize_col(c): c for c in df.columns}
    renamed = {}

    for std_name, aliases in DISPATCH_COLS.items():
        for alias in aliases:
            alias_norm = _normalize_col(alias)
            if alias_norm in col_norm:
                renamed[col_norm[alias_norm]] = std_name
                break

    df = df.rename(columns=renamed)

    # Ensure SampleID exists
    if 'SampleID' not in df.columns:
        # Try to find it by content (SM + digits pattern)
        for c in df.columns:
            if df[c].astype(str).str.match(r'SM\d+|sm\d+', na=False).sum() > len(df) * 0.3:
                df = df.rename(columns={c: 'SampleID'})
                break

    if 'SampleID' not in df.columns:
        return None

    df['SampleID'] = df['SampleID'].astype(str).str.strip()
    df = df[df['SampleID'].notna() & (df['SampleID'] != '') & (df['SampleID'] != 'nan')]

    return df


# ─── SAMPLE TYPE NORMALIZATION ────────────────────────────────────────────────

def normalize_sample_type(row: dict, dispatch_row: dict = None) -> dict:
    """
    Determine SampleType, QAQCType, IDParent, IDBlk, IDStd for a record.
    Priority: dispatch > analytical file fields > heuristic detection.
    """
    result = {
        'SampleType': 'ORIG',
        'QAQCType': None,
        'IDParent': None,
        'IDBlk': None,
        'IDStd': None,
        'QC_Source': 'TES',  # TES = our QC, LAB = lab QC
    }

    # ── From dispatch (highest priority) ──
    if dispatch_row:
        stype = str(dispatch_row.get('SampleType', '')).upper().strip()
        qtype = str(dispatch_row.get('QAQCType', '')).upper().strip()

        if stype == 'QAQC' or qtype in ('DUP', 'BLK', 'STD'):
            result['SampleType'] = 'QAQC'
            if qtype:
                result['QAQCType'] = qtype
            result['IDParent'] = dispatch_row.get('IDParent') or None
            id_blk = dispatch_row.get('IDBlk')
            id_std = dispatch_row.get('IDStd')
            if id_blk and str(id_blk) not in ('nan', 'None', ''):
                result['IDBlk'] = str(id_blk).strip()
            if id_std and str(id_std) not in ('nan', 'None', ''):
                result['IDStd'] = str(id_std).strip()
        else:
            result['SampleType'] = 'ORIG'
        return result

    # ── From analytical file raw fields ──
    sid = str(row.get('SampleID', '')).upper()
    notes_raw = str(row.get('Notes', '')).upper()
    stype_raw = str(row.get('SampleType_raw', '')).upper()
    qtype_raw = str(row.get('QAQCType_raw', '')).upper()
    qname_raw = str(row.get('QAQCName_raw', '')).strip()

    # Vanta VMR has integrated QAQC fields
    if qtype_raw in ('DUP', 'BLK', 'STD', 'BLANK', 'STANDARD', 'DUPLICATE'):
        result['SampleType'] = 'QAQC'
        if 'DUP' in qtype_raw:
            result['QAQCType'] = 'DUP'
        elif 'BLK' in qtype_raw or 'BLANK' in qtype_raw:
            result['QAQCType'] = 'BLK'
            result['IDBlk'] = qname_raw or None
        elif 'STD' in qtype_raw or 'STANDARD' in qtype_raw:
            result['QAQCType'] = 'STD'
            result['IDStd'] = qname_raw or None
        return result

    # Olympus Vanta: Notes column contains BLK/STD/DUP
    if notes_raw in ('BLK', 'BLANK'):
        result['SampleType'] = 'QAQC'
        result['QAQCType'] = 'BLK'
    elif notes_raw in ('STD', 'STANDARD'):
        result['SampleType'] = 'QAQC'
        result['QAQCType'] = 'STD'
    elif notes_raw in ('DUP', 'DUPLICATE'):
        result['SampleType'] = 'QAQC'
        result['QAQCType'] = 'DUP'

    # Heuristic: detect OREAS/CRM names in SampleID
    elif _is_crm_name(sid):
        result['SampleType'] = 'QAQC'
        # Determine if blank or standard
        if _is_blank_crm(sid):
            result['QAQCType'] = 'BLK'
            result['IDBlk'] = sid
        else:
            result['QAQCType'] = 'STD'
            result['IDStd'] = sid
    elif 'DUP' in sid or 'DUPLICATE' in sid:
        result['SampleType'] = 'QAQC'
        result['QAQCType'] = 'DUP'

    # Lab QC detection from record type
    if row.get('RecordType') in ('LAB_DUP', 'LAB_BLK', 'LAB_STD'):
        result['SampleType'] = 'QAQC'
        result['QC_Source'] = 'LAB'
        rt = row['RecordType']
        if rt == 'LAB_DUP':
            result['QAQCType'] = 'DUP'
        elif rt == 'LAB_BLK':
            result['QAQCType'] = 'BLK'
        elif rt == 'LAB_STD':
            result['QAQCType'] = 'STD'

    return result


def _is_crm_name(sid: str) -> bool:
    """Check if a Sample ID looks like a CRM name."""
    patterns = [
        r'OREAS\s*\d', r'AMIS\d', r'GSS\d', r'STSD\d', r'SY-\d',
        r'MEG-LI', r'BLK', r'BLANK', r'STD\s*OREAS', r'STD\s*BLANK',
        r'OREAS\d', r'OR\d{2,}'
    ]
    return any(re.search(p, sid, re.IGNORECASE) for p in patterns)


def _is_blank_crm(sid: str) -> bool:
    """Check if CRM is a blank (near-zero reference)."""
    blank_patterns = ['999b', 'blank', 'blk', 'OREAS20', 'OREAS22']
    sid_low = sid.lower()
    # OREAS 20x and 22x are barren granodiorite — used as blanks in Ta/Nb projects
    return any(p.lower() in sid_low for p in blank_patterns)


# ─── MAIN MERGER ─────────────────────────────────────────────────────────────

def merge_dispatch_and_results(
    parsed_results: list[dict],
    dispatch_df: pd.DataFrame | None = None,
    log: list = None
) -> pd.DataFrame:
    """
    Merge parsed analytical records with dispatch.
    Returns a clean unified DataFrame.
    """
    if log is None:
        log = []

    all_records = []
    for parsed in parsed_results:
        for rec in parsed.get('records', []):
            rec['_source_file'] = parsed.get('source_file', '')
            rec['_format'] = parsed.get('format', '')
            rec['_method_type'] = parsed.get('method_type', '')
            all_records.append(rec)

    if not all_records:
        log.append("⚠ No records to merge")
        return pd.DataFrame()

    df = pd.DataFrame(all_records)
    df['SampleID'] = df['SampleID'].astype(str).str.strip()

    # ── Merge with dispatch ──
    has_dispatch = dispatch_df is not None and not dispatch_df.empty

    if has_dispatch:
        dispatch_df['SampleID'] = dispatch_df['SampleID'].astype(str).str.strip()
        # Merge on SampleID
        df = df.merge(dispatch_df, on='SampleID', how='left', suffixes=('', '_dispatch'))
        log.append(f"✓ Dispatch merged: {len(dispatch_df)} entries")

        # Count matches
        n_matched = df['SampleID'].isin(dispatch_df['SampleID']).sum()
        n_unmatched = len(df) - n_matched
        if n_unmatched > 0:
            log.append(f"⚠ {n_unmatched} records not in dispatch → kept as informational")
    else:
        log.append("ℹ No dispatch provided → degraded mode")

    # ── Normalize sample types ──
    qc_info_list = []
    for idx, row in df.iterrows():
        row_dict = row.to_dict()
        # Build dispatch sub-dict if merged
        dispatch_sub = None
        if has_dispatch:
            d_cols = [c for c in dispatch_df.columns if c != 'SampleID']
            dispatch_sub = {c: row_dict.get(c) for c in d_cols}
        qc_info = normalize_sample_type(row_dict, dispatch_sub if has_dispatch else None)
        qc_info_list.append(qc_info)

    qc_df = pd.DataFrame(qc_info_list)
    for col in qc_df.columns:
        df[col] = qc_df[col].values

    # ── Stats ──
    n_orig = (df['SampleType'] == 'ORIG').sum()
    n_dup = ((df['SampleType'] == 'QAQC') & (df['QAQCType'] == 'DUP')).sum()
    n_blk = ((df['SampleType'] == 'QAQC') & (df['QAQCType'] == 'BLK')).sum()
    n_std = ((df['SampleType'] == 'QAQC') & (df['QAQCType'] == 'STD')).sum()
    n_lab = (df.get('QC_Source', pd.Series(['TES'] * len(df))) == 'LAB').sum()

    log.append(f"✓ Records: {n_orig} ORIG / {n_dup} DUP / {n_blk} BLK / {n_std} STD")
    if n_lab > 0:
        log.append(f"ℹ {n_lab} lab QC records (informational)")

    return df
