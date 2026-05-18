"""
parser.py — Multi-format geochemical data parser
Supports: Olympus Vanta pXRF (CSV/XLSX), Bruker Vanta VMR, ALS/MSALabs ICP (XLS/CSV),
          MSALABS multi-block CSV, SciAps LIBS, generic fallback
"""

import pandas as pd
import numpy as np
import chardet
import hashlib
import json
import os
import re
from pathlib import Path

# ─── Element symbol normalization ────────────────────────────────────────────
ELEMENT_ALIASES = {
    "tantalum": "Ta", "niobium": "Nb", "titanium": "Ti", "rubidium": "Rb",
    "lithium": "Li", "copper": "Cu", "lead": "Pb", "zinc": "Zn",
    "nickel": "Ni", "cobalt": "Co", "manganese": "Mn", "iron": "Fe",
    "aluminum": "Al", "aluminium": "Al", "silicon": "Si", "calcium": "Ca",
    "magnesium": "Mg", "potassium": "K", "vanadium": "V", "chromium": "Cr",
    "zirconium": "Zr", "yttrium": "Y", "strontium": "Sr", "barium": "Ba",
    "arsenic": "As", "tungsten": "W", "tin": "Sn", "bismuth": "Bi",
    "uranium": "U", "thorium": "Th", "lanthanum": "La", "cerium": "Ce",
    "praseodymium": "Pr", "neodymium": "Nd", "samarium": "Sm",
    "silver": "Ag", "gold": "Au", "mercury": "Hg", "antimony": "Sb",
    "selenium": "Se", "molybdenum": "Mo", "cadmium": "Cd", "indium": "In",
    "tellurium": "Te", "cesium": "Cs", "beryllium": "Be", "boron": "B",
    "gallium": "Ga", "germanium": "Ge", "hafnium": "Hf", "rhenium": "Re",
    "scandium": "Sc", "thallium": "Tl", "phosphorus": "P", "sulfur": "S",
    "sodium": "Na", "dysprosium": "Dy", "erbium": "Er", "europium": "Eu",
    "gadolinium": "Gd", "holmium": "Ho", "lutetium": "Lu", "terbium": "Tb",
    "thulium": "Tm", "ytterbium": "Yb",
}

KNOWN_ELEMENTS = set([
    "Ag","Al","As","Au","B","Ba","Be","Bi","Ca","Cd","Ce","Co","Cr","Cs",
    "Cu","Dy","Er","Eu","Fe","Ga","Gd","Ge","Hf","Hg","Ho","In","K","La",
    "Li","Lu","Mg","Mn","Mo","Na","Nb","Nd","Ni","P","Pb","Pr","Rb","Re",
    "S","Sb","Sc","Se","Si","Sm","Sn","Sr","Ta","Tb","Te","Th","Ti","Tl",
    "Tm","U","V","W","Y","Yb","Zn","Zr","LE"
])

BASE_DIR = Path(__file__).parent.parent
MEMORY_PATH = BASE_DIR / "memory.json"


def normalize_element(name: str) -> str | None:
    """Normalize element name to standard symbol."""
    if not name:
        return None
    n = str(name).strip()
    # Direct match
    if n in KNOWN_ELEMENTS:
        return n
    # Upper first letter
    cap = n.capitalize()
    if cap in KNOWN_ELEMENTS:
        return cap
    # All upper → try capitalize
    if n.upper() == n and n.lower() in ELEMENT_ALIASES:
        return ELEMENT_ALIASES[n.lower()]
    # Alias lookup
    low = n.lower()
    if low in ELEMENT_ALIASES:
        return ELEMENT_ALIASES[low]
    return None


def parse_value(v) -> float | None:
    """Convert a raw value to float. Handles <LOD, ND, <X, >X patterns."""
    if v is None:
        return None
    s = str(v).strip()
    if s in ("", "nan", "NaN", "NaT", "-", "N/A", "n/a"):
        return None
    # <LOD or ND → return 0 (below detection)
    if s.upper() in ("<LOD", "LOD", "ND", "BDL", "BLD", "<DL"):
        return 0.0
    # <X → half of X
    m = re.match(r'^[<>]?\s*([\d.]+)', s)
    if m:
        try:
            val = float(m.group(1))
            return val / 2 if s.startswith('<') else val
        except ValueError:
            pass
    # "1469.6 ppm" style from LIBS
    m2 = re.match(r'^([\d.]+)\s*ppm', s)
    if m2:
        try:
            return float(m2.group(1))
        except ValueError:
            pass
    try:
        return float(s)
    except (ValueError, TypeError):
        return None


def detect_encoding(filepath: str) -> str:
    """Detect file encoding."""
    with open(filepath, 'rb') as f:
        raw = f.read(50000)
    result = chardet.detect(raw)
    enc = result.get('encoding') or 'utf-8'
    # Normalize common variants
    enc = enc.replace('ISO-8859-1', 'latin-1').replace('Windows-1252', 'cp1252')
    return enc


def file_signature(filepath: str) -> str:
    """Compute a short hash of first 2KB for format memory."""
    with open(filepath, 'rb') as f:
        head = f.read(2048)
    return hashlib.md5(head).hexdigest()[:16]


def load_memory() -> dict:
    try:
        with open(MEMORY_PATH) as f:
            return json.load(f)
    except Exception:
        return {"formats": {}, "crm_patterns": {}, "zones": {}}


def save_memory(mem: dict):
    try:
        with open(MEMORY_PATH, 'w') as f:
            json.dump(mem, f, indent=2, ensure_ascii=False)
    except Exception:
        pass


# ─── FORMAT DETECTORS ────────────────────────────────────────────────────────

def _is_vanta_olympus_csv(lines: list[str]) -> bool:
    """Olympus Vanta: semicolon CSV with 'Instrument Serial Num' first column."""
    if not lines:
        return False
    first = lines[0]
    return ('Instrument Serial Num' in first and
            'Reading #' in first and
            ';' in first)


def _is_vanta_vmr(lines: list[str]) -> bool:
    """Bruker Vanta VMR: semicolon CSV with 'Reading;Mode;Elapsed Time'."""
    if not lines:
        return False
    first = lines[0]
    return ('Reading;Mode' in first or
            ('Date;Time;Reading' in first and 'Elapsed Time' in first))


def _is_als_icp_xls(wb_sheets: list[str]) -> bool:
    """ALS/MSALabs XLS: has 'Analytical Data' sheet."""
    return any('Analytical Data' in s or 'analytical' in s.lower()
               for s in wb_sheets)


def _is_msalabs_multiblock_csv(lines: list[str]) -> bool:
    """MSALABS multi-block report: 'TEST REPORT' and 'MSALABS' in first lines."""
    head = '\n'.join(lines[:10])
    return 'MSALABS' in head and 'TEST REPORT' in head


def _is_libs_sciaps(lines: list[str]) -> bool:
    """SciAps LIBS: has 'Test #' and 'Li' column, comma-separated."""
    if not lines:
        return False
    first = lines[0]
    return ('Test #' in first or 'Test#' in first) and 'Li' in first


# ─── FORMAT PARSERS ──────────────────────────────────────────────────────────

def _parse_vanta_olympus(filepath: str, ext: str) -> dict:
    """
    Parse Olympus Vanta pXRF export.
    Format: Each element has 7 columns (Compound/Level/Error/Concentration/Error1s/Slope/Offset)
    Last columns: Sample ID, Project No., Sample Type, Operator, Notes
    """
    enc = detect_encoding(filepath)
    if ext in ('.xlsx', '.xls'):
        df_raw = pd.read_excel(filepath, header=0)
    else:
        df_raw = pd.read_csv(filepath, sep=';', encoding=enc, header=0)

    records = []
    cols = list(df_raw.columns)

    # Find element concentration columns: "El Concentration"
    elem_conc = {}  # element → col_index
    for i, c in enumerate(cols):
        if 'Concentration' in str(c):
            parts = str(c).split()
            if parts:
                el = normalize_element(parts[0])
                if el:
                    elem_conc[el] = i

    # Find metadata columns
    def find_col(names):
        for name in names:
            for i, c in enumerate(cols):
                if name.lower() in str(c).lower():
                    return i
        return None

    idx_sid = find_col(['Sample ID', 'SampleID'])
    idx_proj = find_col(['Project No'])
    idx_stype = find_col(['Sample Type'])
    idx_oper = find_col(['Operator'])
    idx_notes = find_col(['Notes'])
    idx_date = find_col(['Date'])
    idx_time = find_col(['Time'])
    idx_reading = find_col(['Reading #', 'Reading#'])
    idx_method = find_col(['Method Name'])
    idx_sn = find_col(['Instrument Serial Num', 'Instrument SN'])
    idx_lat = find_col(['Latitude'])
    idx_lon = find_col(['Longitude'])

    for _, row in df_raw.iterrows():
        vals = row.values

        # Skip calibration rows (no Sample ID or empty)
        raw_sid = vals[idx_sid] if idx_sid is not None else None
        if pd.isna(raw_sid) or str(raw_sid).strip() in ('', 'nan'):
            continue

        rec = {
            'SampleID': str(raw_sid).strip(),
            'Method': 'pXRF',
            'Instrument': 'Olympus Vanta',
            'Date': str(vals[idx_date]).strip() if idx_date is not None else '',
            'ProjectNo': str(vals[idx_proj]).strip() if idx_proj is not None else '',
            'SampleType_raw': str(vals[idx_stype]).strip() if idx_stype is not None else '',
            'Operator': str(vals[idx_oper]).strip() if idx_oper is not None else '',
            'Notes': str(vals[idx_notes]).strip() if idx_notes is not None else '',
            'Reading': vals[idx_reading] if idx_reading is not None else None,
            'Latitude': parse_value(vals[idx_lat]) if idx_lat is not None else None,
            'Longitude': parse_value(vals[idx_lon]) if idx_lon is not None else None,
            'MethodName': str(vals[idx_method]).strip() if idx_method is not None else '',
        }

        # Extract element values
        for el, ci in elem_conc.items():
            rec[f'{el}_ppm'] = parse_value(vals[ci])

        records.append(rec)

    return {
        'records': records,
        'format': 'Olympus_Vanta_pXRF',
        'method_type': 'pXRF',
        'instrument': 'Olympus Vanta',
        'elements': list(elem_conc.keys()),
        'n_records': len(records)
    }


def _parse_vanta_vmr(filepath: str) -> dict:
    """
    Parse Bruker Vanta VMR pXRF export.
    Has integrated dispatch fields: Sample ID, Sample Type, Hole ID, Depth From/To, QAQC Type/Name
    Elements are direct columns: El, El +/-, El Intercept, El Slope
    """
    enc = detect_encoding(filepath)
    df_raw = pd.read_csv(filepath, sep=';', encoding=enc, header=0)
    cols = list(df_raw.columns)

    # Find element columns: those that are pure element symbols (no +/- etc.)
    elem_cols = {}
    for c in cols:
        cs = str(c).strip()
        el = normalize_element(cs)
        if el and f'{cs} +/-' in cols:  # has error column → it's an element
            elem_cols[el] = cs

    def find_col(names):
        for name in names:
            for c in cols:
                if name.lower() in str(c).lower():
                    return c
        return None

    col_sid = find_col(['Sample ID', 'SampleID'])
    col_stype = find_col(['Sample Type'])
    col_hole = find_col(['Hole ID'])
    col_from = find_col(['Depth From', 'From'])
    col_to = find_col(['Depth To', 'To'])
    col_east = find_col(['East'])
    col_north = find_col(['North'])
    col_qaqc_type = find_col(['QAQC Type'])
    col_qaqc_name = find_col(['QAQC Name'])
    col_project = find_col(['Project ID'])
    col_prospect = find_col(['Prospect ID'])
    col_date = find_col(['Date'])
    col_sampler = find_col(['Sampler Name'])
    col_mode = find_col(['Mode'])
    col_model = find_col(['Model'])
    col_comments = find_col(['Comments'])

    records = []
    for _, row in df_raw.iterrows():
        sid = row.get(col_sid, '') if col_sid else ''
        if pd.isna(sid) or str(sid).strip() in ('', 'nan'):
            continue

        rec = {
            'SampleID': str(sid).strip(),
            'Method': 'pXRF',
            'Instrument': f"Vanta VMR {row.get(col_model,'') if col_model else ''}".strip(),
            'Date': str(row.get(col_date, '')) if col_date else '',
            'ProjectNo': str(row.get(col_project, '')) if col_project else '',
            'ProspectID': str(row.get(col_prospect, '')) if col_prospect else '',
            'SampleType_raw': str(row.get(col_stype, '')) if col_stype else '',
            'HoleID': str(row.get(col_hole, '')) if col_hole else '',
            'From_m': parse_value(row.get(col_from)) if col_from else None,
            'To_m': parse_value(row.get(col_to)) if col_to else None,
            'East': parse_value(row.get(col_east)) if col_east else None,
            'North': parse_value(row.get(col_north)) if col_north else None,
            'QAQCType_raw': str(row.get(col_qaqc_type, '')) if col_qaqc_type else '',
            'QAQCName_raw': str(row.get(col_qaqc_name, '')) if col_qaqc_name else '',
            'Sampler': str(row.get(col_sampler, '')) if col_sampler else '',
            'Comments': str(row.get(col_comments, '')) if col_comments else '',
            'MethodName': str(row.get(col_mode, '')) if col_mode else '',
            # VMR has integrated dispatch
            '_has_dispatch_fields': True,
        }

        for el, col in elem_cols.items():
            rec[f'{el}_ppm'] = parse_value(row.get(col))

        records.append(rec)

    return {
        'records': records,
        'format': 'Bruker_Vanta_VMR_pXRF',
        'method_type': 'pXRF',
        'instrument': 'Bruker Vanta VMR',
        'elements': list(elem_cols.keys()),
        'has_integrated_dispatch': True,
        'n_records': len(records)
    }


def _parse_als_icp_xls(filepath: str) -> dict:
    """
    Parse ALS / MSALabs ICP XLS report.
    Structure: 8 header rows, then data. Sheets: Analytical Data, Lab Duplicates, Lab Standards
    Row 6: method codes, Row 7: element names, Row 8: units, Row 9+: data
    """
    ext = Path(filepath).suffix.lower()
    engine = 'xlrd' if ext == '.xls' else 'openpyxl'

    xf = pd.ExcelFile(filepath, engine=engine)
    all_records = []
    method_name = 'ME-MS89L'  # default, will be detected

    sheet_map = {
        'analytical': 'ORIG',
        'lab dup': 'LAB_DUP',
        'lab standard': 'LAB_STD',
    }

    for sheet in xf.sheet_names:
        sheet_lower = sheet.lower()
        rec_type = 'ORIG'
        for key, val in sheet_map.items():
            if key in sheet_lower:
                rec_type = val
                break

        df_raw = pd.read_excel(filepath, engine=engine, sheet_name=sheet, header=None)
        if df_raw.shape[0] < 9:
            continue

        # Find the header rows
        # Row with element names (row 7, 0-indexed = 7)
        # Find where "SAMPLE" or "DESCRIPTION" appears
        header_row = None
        for ri in range(min(15, df_raw.shape[0])):
            row_vals = [str(v).upper() for v in df_raw.iloc[ri].values]
            if 'SAMPLE' in row_vals or any('DESCRIPTION' in v for v in row_vals):
                header_row = ri
                break
        if header_row is None:
            continue

        # Method row is usually 1 before header_row
        method_row = header_row - 1
        unit_row = header_row + 1
        data_start = header_row + 2

        elem_names = list(df_raw.iloc[header_row].values)
        unit_vals = list(df_raw.iloc[unit_row].values) if unit_row < df_raw.shape[0] else []
        method_vals = list(df_raw.iloc[method_row].values) if method_row >= 0 else []

        # Detect method from method row
        for v in method_vals:
            vs = str(v).strip()
            if re.match(r'ME-|IMS-|4A|ICP', vs, re.IGNORECASE) and len(vs) > 2:
                method_name = vs
                break

        # Build column mapping: col_idx → (element, unit)
        col_map = {}  # col_idx → element
        unit_map = {}  # element → unit
        sample_col = 0

        for ci, en in enumerate(elem_names):
            ens = str(en).strip()
            if ens.upper() in ('SAMPLE', 'DESCRIPTION', 'SAMPLE DESCRIPTION', ''):
                sample_col = ci
                continue
            el = normalize_element(ens)
            if el:
                col_map[ci] = el
                unit_str = str(unit_vals[ci]).strip() if ci < len(unit_vals) else 'ppm'
                unit_map[el] = unit_str

        for ri in range(data_start, df_raw.shape[0]):
            row = df_raw.iloc[ri].values
            sid = str(row[sample_col]).strip()
            if not sid or sid in ('nan', 'NaN', '') or sid.startswith('STD') or 'BLANK' == sid.upper():
                continue

            rec = {
                'SampleID': sid,
                'Method': method_name,
                'Instrument': 'ICP-MS',
                'RecordType': rec_type,
                'SheetName': sheet,
            }

            for ci, el in col_map.items():
                if ci >= len(row):
                    continue
                val = parse_value(row[ci])
                # Convert % to ppm
                unit = unit_map.get(el, 'ppm')
                if unit == '%' and val is not None:
                    val = val * 10000
                rec[f'{el}_ppm'] = val

            all_records.append(rec)

    return {
        'records': all_records,
        'format': 'ALS_MSALabs_ICP_XLS',
        'method_type': 'ICP-MS',
        'method_name': method_name,
        'instrument': 'ICP-MS',
        'elements': list(set(el for r in all_records for el in [
            k.replace('_ppm', '') for k in r.keys() if k.endswith('_ppm')])),
        'n_records': len(all_records)
    }


def _parse_msalabs_multiblock(filepath: str) -> dict:
    """
    Parse MSALABS multi-block CSV (YAM-style).
    Multiple copies of the report side by side (each 13 cols wide).
    Header: row 12 = Sample/Type/Method, row 13 = analytes, row 14 = units, row 15 = LOR
    Data: row 16+
    """
    enc = detect_encoding(filepath)
    with open(filepath, 'r', encoding=enc, errors='replace') as f:
        lines = f.readlines()

    # Find header rows
    header_row_idx = None
    for i, line in enumerate(lines):
        if ',Sample ,' in line or ',Sample,' in line:
            header_row_idx = i
            break

    if header_row_idx is None:
        return {'records': [], 'format': 'MSALABS_multiblock', 'error': 'Header not found'}

    # Parse as CSV from the beginning to get full structure
    df_raw = pd.read_csv(filepath, header=None, encoding=enc, on_bad_lines='skip')

    analyte_row = header_row_idx + 1
    unit_row = header_row_idx + 2
    data_start = header_row_idx + 4  # skip LOR row

    # Parse analyte names from row analyte_row
    # The CSV has blocks repeated; first block is what we need
    # Find end of data: blank rows or duplicate section markers
    analyte_names = list(df_raw.iloc[analyte_row].values) if analyte_row < df_raw.shape[0] else []
    unit_names = list(df_raw.iloc[unit_row].values) if unit_row < df_raw.shape[0] else []

    # Build column map for first block only (until repeated "Sample ID" appears)
    col_map = {}
    unit_map = {}
    sample_col = None
    type_col = None
    weight_col = None

    for ci, an in enumerate(analyte_names):
        ans = str(an).strip()
        if ans.lower() in ('sample id', 'sample', ''):
            if sample_col is None:
                sample_col = ci
            continue
        if ans.lower() == 'type':
            type_col = ci
            continue
        if ans.lower() in ('rec. wt.', 'rec wt', 'weight'):
            weight_col = ci
            continue
        # Stop at repeated block (second "Sample ID")
        if ans.lower() == 'sample id' and sample_col is not None:
            break
        el = normalize_element(ans)
        if el:
            col_map[ci] = el
            unit_map[el] = str(unit_names[ci]).strip() if ci < len(unit_names) else 'ppm'

    if sample_col is None:
        sample_col = 0

    records = []
    method_name = 'IMS-230'

    for ri in range(data_start, df_raw.shape[0]):
        row = df_raw.iloc[ri].values
        sid_raw = str(row[sample_col]).strip() if sample_col < len(row) else ''
        if not sid_raw or sid_raw in ('nan', 'NaN', '', 'Sample ID'):
            continue

        # Stop at blank separator
        if all(str(v).strip() in ('', 'nan') for v in row[:5]):
            continue

        rec = {
            'SampleID': sid_raw,
            'Method': method_name,
            'Instrument': 'ICP-MS',
            'RecordType': 'ORIG',
            'SampleType_raw': str(row[type_col]).strip() if type_col and type_col < len(row) else '',
            'Mass_kg': parse_value(row[weight_col]) if weight_col and weight_col < len(row) else None,
        }

        for ci, el in col_map.items():
            if ci >= len(row):
                continue
            val = parse_value(row[ci])
            unit = unit_map.get(el, 'ppm')
            if unit == '%' and val is not None:
                val = val * 10000
            rec[f'{el}_ppm'] = val

        # Check if it's a QC row based on Sample ID prefix
        sid_up = sid_raw.upper()
        if 'DUP' in sid_up:
            rec['RecordType'] = 'LAB_DUP'
        elif 'STD' in sid_up or 'OREAS' in sid_up:
            rec['RecordType'] = 'LAB_STD'
        elif 'BLANK' in sid_up or 'BLK' in sid_up:
            rec['RecordType'] = 'LAB_BLK'

        records.append(rec)

    return {
        'records': records,
        'format': 'MSALABS_multiblock_CSV',
        'method_type': 'ICP-MS',
        'method_name': method_name,
        'instrument': 'ICP-MS',
        'elements': list(col_map.values()),
        'n_records': len(records)
    }


def _parse_libs_sciaps(filepath: str) -> dict:
    """
    Parse SciAps LIBS export.
    Format: Date, Test#, Sample ID, Grade Match, Unit type, Mode, LOD Sigma, ..., Li
    Values: "1469.6 ppm" or "ND"
    """
    enc = detect_encoding(filepath)

    # The format has weird quoting — each line is a single quoted field
    records = []
    with open(filepath, 'r', encoding=enc, errors='replace') as f:
        lines = f.readlines()

    header = None
    for line in lines:
        # Clean the line from outer quotes
        line = line.strip().strip('"')
        if not line:
            continue
        parts = [p.strip().strip('"') for p in line.split(',')]
        if header is None:
            if 'Sample ID' in parts or 'Test #' in parts or 'Test#' in parts:
                header = parts
            continue

        if len(parts) < 3:
            continue

        rec_dict = dict(zip(header, parts))
        sid = rec_dict.get('Sample ID', '').strip()
        if not sid or sid in ('nan', ''):
            continue

        li_raw = rec_dict.get('Li', 'ND').strip()
        li_val = parse_value(li_raw)

        rec = {
            'SampleID': sid,
            'Method': 'LIBS',
            'Instrument': 'SciAps LIBS',
            'Date': rec_dict.get('Date', ''),
            'TestNo': rec_dict.get('Test #', rec_dict.get('Test#', '')),
            'Li_ppm': li_val,
        }
        records.append(rec)

    return {
        'records': records,
        'format': 'SciAps_LIBS',
        'method_type': 'LIBS',
        'instrument': 'SciAps LIBS',
        'elements': ['Li'],
        'n_records': len(records)
    }


def _parse_generic(filepath: str) -> dict:
    """Generic fallback: try to find SampleID + element columns."""
    enc = detect_encoding(filepath)
    ext = Path(filepath).suffix.lower()

    try:
        if ext in ('.xlsx', '.xlsm'):
            df = pd.read_excel(filepath, header=None)
        elif ext == '.xls':
            df = pd.read_excel(filepath, engine='xlrd', header=None)
        else:
            # Try different separators
            for sep in [',', ';', '\t', '|']:
                try:
                    df = pd.read_csv(filepath, sep=sep, encoding=enc, header=None, nrows=5)
                    if df.shape[1] > 2:
                        df = pd.read_csv(filepath, sep=sep, encoding=enc, header=None)
                        break
                except Exception:
                    continue
    except Exception as e:
        return {'records': [], 'format': 'unknown', 'error': str(e)}

    # Find header row: one with most element names
    best_row = 0
    best_count = 0
    for ri in range(min(20, df.shape[0])):
        count = sum(1 for v in df.iloc[ri].values if normalize_element(str(v)))
        if count > best_count:
            best_count = count
            best_row = ri

    if best_count < 2:
        return {'records': [], 'format': 'generic_failed', 'n_records': 0}

    header = list(df.iloc[best_row].values)
    col_map = {}
    sample_col = None

    for ci, h in enumerate(header):
        hs = str(h).strip()
        if hs.lower() in ('sample', 'sample id', 'sampleid', 'sample_id', 'id'):
            sample_col = ci
            continue
        el = normalize_element(hs)
        if el:
            col_map[ci] = el

    if sample_col is None:
        sample_col = 0

    records = []
    for ri in range(best_row + 2, df.shape[0]):
        row = df.iloc[ri].values
        sid = str(row[sample_col]).strip() if sample_col < len(row) else ''
        if not sid or sid in ('nan', 'NaN', ''):
            continue
        rec = {'SampleID': sid, 'Method': 'unknown', 'Instrument': 'unknown'}
        for ci, el in col_map.items():
            if ci < len(row):
                rec[f'{el}_ppm'] = parse_value(row[ci])
        records.append(rec)

    return {
        'records': records,
        'format': 'generic',
        'method_type': 'unknown',
        'elements': list(col_map.values()),
        'n_records': len(records)
    }


# ─── MAIN PARSE FUNCTION ─────────────────────────────────────────────────────

def parse_file(filepath: str, log: list = None) -> dict:
    """
    Parse any geochemical file. Returns standardized dict with records.
    Uses memory.json to speed up re-detection.
    """
    if log is None:
        log = []

    path = Path(filepath)
    ext = path.suffix.lower()
    sig = file_signature(filepath)
    mem = load_memory()

    # Check memory first
    if sig in mem.get('formats', {}):
        known = mem['formats'][sig]
        log.append(f"✓ Format recognized from memory: {known['format']} ({known['nb_fois']}x seen)")
        known['nb_fois'] += 1
        save_memory(mem)

    log.append(f"Parsing: {path.name}")

    result = None

    # ── XLS/XLSX branch ──
    if ext in ('.xls', '.xlsx', '.xlsm'):
        if ext == '.xls':
            try:
                xf = pd.ExcelFile(filepath, engine='xlrd')
                if _is_als_icp_xls(xf.sheet_names):
                    log.append("→ Format: ALS/MSALabs ICP XLS (multi-sheet)")
                    result = _parse_als_icp_xls(filepath)
                else:
                    # Could be Vanta XLSX
                    df_peek = pd.read_excel(filepath, engine='xlrd', nrows=1)
                    cols_str = ' '.join(str(c) for c in df_peek.columns)
                    if 'Instrument Serial Num' in cols_str:
                        log.append("→ Format: Olympus Vanta pXRF (XLS)")
                        result = _parse_vanta_olympus(filepath, ext)
                    else:
                        log.append("→ Format: ALS ICP XLS (fallback)")
                        result = _parse_als_icp_xls(filepath)
            except Exception as e:
                log.append(f"⚠ XLS parse error: {e}")
                result = _parse_generic(filepath)
        else:
            try:
                xf = pd.ExcelFile(filepath)
                if _is_als_icp_xls(xf.sheet_names):
                    log.append("→ Format: ALS/MSALabs ICP XLSX")
                    result = _parse_als_icp_xls(filepath)
                else:
                    df_peek = pd.read_excel(filepath, nrows=1)
                    cols_str = ' '.join(str(c) for c in df_peek.columns)
                    if 'Instrument Serial Num' in cols_str:
                        log.append("→ Format: Olympus Vanta pXRF (XLSX)")
                        result = _parse_vanta_olympus(filepath, ext)
                    else:
                        log.append("→ Format: generic XLSX")
                        result = _parse_generic(filepath)
            except Exception as e:
                log.append(f"⚠ XLSX parse error: {e}")
                result = _parse_generic(filepath)

    # ── CSV/TXT branch ──
    elif ext in ('.csv', '.txt'):
        enc = detect_encoding(filepath)
        try:
            with open(filepath, 'r', encoding=enc, errors='replace') as f:
                lines = f.readlines()
        except Exception:
            lines = []

        if _is_vanta_olympus_csv(lines):
            log.append("→ Format: Olympus Vanta pXRF (CSV semicolon)")
            result = _parse_vanta_olympus(filepath, ext)
        elif _is_vanta_vmr(lines):
            log.append("→ Format: Bruker Vanta VMR pXRF (CSV semicolon)")
            result = _parse_vanta_vmr(filepath)
        elif _is_msalabs_multiblock_csv(lines):
            log.append("→ Format: MSALABS multi-block ICP CSV")
            result = _parse_msalabs_multiblock(filepath)
        elif _is_libs_sciaps(lines):
            log.append("→ Format: SciAps LIBS (Li)")
            result = _parse_libs_sciaps(filepath)
        else:
            log.append("→ Format: generic CSV (fallback)")
            result = _parse_generic(filepath)
    else:
        log.append(f"→ Unknown extension {ext}, trying generic")
        result = _parse_generic(filepath)

    if result is None:
        result = {'records': [], 'format': 'unknown', 'n_records': 0}

    # Save to memory
    result['source_file'] = path.name
    result['log'] = log

    if result.get('format', 'unknown') not in ('unknown', 'generic_failed'):
        mem_formats = mem.setdefault('formats', {})
        if sig not in mem_formats:
            mem_formats[sig] = {
                'format': result['format'],
                'method_type': result.get('method_type', ''),
                'instrument': result.get('instrument', ''),
                'source_file': path.name,
                'nb_fois': 1
            }
        save_memory(mem)

    log.append(f"✓ Parsed {result['n_records']} records — elements: {result.get('elements', [])[:8]}")
    return result


def parse_multiple_files(filepaths: list[str]) -> dict:
    """Parse multiple files and merge results by format type."""
    all_results = []
    combined_log = []

    for fp in filepaths:
        r = parse_file(fp, log=[])
        all_results.append(r)
        combined_log.extend(r.get('log', []))

    return {
        'files': all_results,
        'log': combined_log,
        'total_records': sum(r['n_records'] for r in all_results)
    }
