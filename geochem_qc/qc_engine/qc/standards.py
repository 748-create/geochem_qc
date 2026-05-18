"""
standards.py — Standards QC: %recovery Shewhart charts, drift detection.
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
    for path in [OREAS_PATH, CUSTOM_CRM_PATH]:
        try:
            with open(path) as f:
                db = json.load(f)
            if crm_id in db:
                methods = db[crm_id].get('methods', {})
                if method in methods:
                    return methods[method]
                for m in ['pXRF', 'ME-MS89L', 'IMS-230', 'fusion_peroxyde']:
                    if m in methods:
                        return methods[m]
        except Exception:
            continue
    return {}


def _normalize_crm_name(raw: str) -> str:
    import re
    s = re.sub(r'\s+', '', str(raw).strip().upper())
    aliases = {
        'OREAS20A': 'OREAS20a', 'OR20A': 'OREAS20a',
        'OREAS22E': 'OREAS22e', 'OR22E': 'OREAS22e',
        'OREAS750B': 'OREAS750b', 'OREAS751B': 'OREAS751b',
        'OREAS752B': 'OREAS752b', 'OREAS753B': 'OREAS753b',
        'OREAS754': 'OREAS754', 'OREAS999B': 'OREAS999b',
        'OREAS601': 'OREAS601', 'AMIS0341BIS': 'AMIS0341bis',
        'AMIS0341': 'AMIS0341bis',
    }
    return aliases.get(s, s)


def calc_recovery(measured: float, certified: float) -> float | None:
    if certified is None or certified == 0:
        return None
    return (measured / certified) * 100


def run_standards_qc(df: pd.DataFrame, log: list = None) -> dict:
    """
    Run standards QC: %recovery for each element vs certified values.
    Checks: accuracy, precision, drift over sequence.
    """
    if log is None:
        log = []

    cfg = load_config()
    seuils = cfg['seuils']
    method_type = df.get('_method_type', pd.Series(['unknown'])).iloc[0] if '_method_type' in df.columns else 'ICP'

    # Use wider tolerances for pXRF
    is_pxrf = 'pXRF' in str(method_type).upper() or (
        df['Method'].str.contains('pXRF|Vanta|VMR', case=False, na=False).any()
        if 'Method' in df.columns else False
    )

    if is_pxrf:
        pass_min = seuils.get('std_pxrf_pass_min', 80)
        pass_max = seuils.get('std_pxrf_pass_max', 120)
        warn_min = 75
        warn_max = 125
    else:
        pass_min = seuils.get('std_pass_min', 90)
        pass_max = seuils.get('std_pass_max', 110)
        warn_min = seuils.get('std_warn_min', 85)
        warn_max = seuils.get('std_warn_max', 115)

    drift_points = seuils.get('drift_points', 3)
    elements_prioritaires = cfg.get('elements_prioritaires', ['Ta', 'Nb', 'Ti', 'Rb'])
    elements_actifs = cfg.get('elements_actifs', [])

    # TES standards only
    std_mask = (df['SampleType'] == 'QAQC') & (df['QAQCType'] == 'STD') & (df['QC_Source'] == 'TES')
    std_df = df[std_mask].copy().reset_index(drop=True)

    if std_df.empty:
        log.append("ℹ No TES standards found")
        return {'crm_results': {}, 'n_standards': 0}

    # Group by CRM
    crm_groups = {}
    for _, row in std_df.iterrows():
        crm_id = row.get('IDStd') or row.get('SampleID', 'UNKNOWN')
        crm_id = _normalize_crm_name(str(crm_id))
        crm_groups.setdefault(crm_id, []).append(row)

    active_elements = list(dict.fromkeys(elements_prioritaires + [
        e for e in elements_actifs if e not in elements_prioritaires]))

    all_results = {}

    for crm_id, rows in crm_groups.items():
        crm_df = pd.DataFrame(rows)
        method = crm_df.iloc[0].get('Method', 'pXRF' if is_pxrf else 'ME-MS89L')
        method_key = 'pXRF' if is_pxrf else method
        certified = load_certified(crm_id, method_key)

        crm_result = {
            'crm_id': crm_id,
            'n_measurements': len(crm_df),
            'method': method,
            'is_pxrf': is_pxrf,
            'elements': {},
            'overall_status': 'PASS',
            'shewhart_data': {},
            'no_certified': []
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

            if cert_val is None or cert_val == 0:
                crm_result['no_certified'].append(el)
                continue

            recoveries = [calc_recovery(float(v), cert_val) for v in vals if v is not None]
            recoveries = [r for r in recoveries if r is not None]

            if not recoveries:
                continue

            mean_rec = float(np.mean(recoveries))
            std_rec = float(np.std(recoveries)) if len(recoveries) > 1 else 0.0

            el_result = {
                'n': len(recoveries),
                'mean_recovery': round(mean_rec, 2),
                'std_recovery': round(std_rec, 2),
                'recoveries': [round(r, 2) for r in recoveries],
                'values_measured': [round(float(v), 4) for v in vals],
                'certified': round(cert_val, 4),
                'certified_sd': round(cert_sd, 4) if cert_sd else None,
                'status': 'PASS',
                'issues': []
            }

            # Status
            n_fail = sum(1 for r in recoveries if r < pass_min or r > pass_max)
            n_warn = sum(1 for r in recoveries
                        if (warn_min <= r < pass_min) or (pass_max < r <= warn_max))

            if n_fail > 0:
                el_result['status'] = 'FAIL'
                el_result['issues'].append(f'{n_fail} measurements outside {pass_min}-{pass_max}%')
                crm_result['overall_status'] = 'FAIL'
            elif n_warn > 0:
                el_result['status'] = 'WARN'
                el_result['issues'].append(f'{n_warn} measurements in warning zone')
                if crm_result['overall_status'] == 'PASS':
                    crm_result['overall_status'] = 'WARN'

            # Drift
            if len(recoveries) >= drift_points:
                x = np.arange(len(recoveries), dtype=float)
                slope = np.polyfit(x, recoveries, 1)[0]
                if abs(slope) > 2.0:  # >2% recovery drift per measurement
                    direction = '↗' if slope > 0 else '↘'
                    el_result['drift'] = {
                        'slope_pct_per_measure': round(float(slope), 3),
                        'direction': direction
                    }
                    el_result['issues'].append(f'DRIFT {direction}: {round(abs(slope),2)}%/measurement')
                    if el in elements_prioritaires and el_result['status'] == 'PASS':
                        el_result['status'] = 'WARN'

            # Shewhart data
            crm_result['shewhart_data'][el] = {
                'recoveries': el_result['recoveries'],
                'mean': round(mean_rec, 2),
                'target': 100.0,
                'ucl': pass_max,
                'uwl': warn_max,
                'lwl': warn_min,
                'lcl': pass_min,
            }

            crm_result['elements'][el] = el_result

        all_results[crm_id] = crm_result

    n_pass = sum(1 for r in all_results.values() if r['overall_status'] == 'PASS')
    n_warn = sum(1 for r in all_results.values() if r['overall_status'] == 'WARN')
    n_fail = sum(1 for r in all_results.values() if r['overall_status'] == 'FAIL')

    log.append(f"✓ STD QC: {len(all_results)} CRMs — {n_pass} PASS / {n_warn} WARN / {n_fail} FAIL")

    return {
        'crm_results': all_results,
        'n_standards': len(std_df),
        'n_crms': len(all_results),
        'n_pass': n_pass, 'n_warn': n_warn, 'n_fail': n_fail,
        'pass_range': [pass_min, pass_max],
        'warn_range': [warn_min, warn_max],
        'is_pxrf': is_pxrf
    }
