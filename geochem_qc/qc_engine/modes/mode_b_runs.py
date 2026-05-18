"""mode_b_runs.py — Mode B implementation: compare two instrumental runs."""
import pandas as pd
import numpy as np
from typing import Dict, List

def run_mode_b(df: pd.DataFrame, log: list = None) -> dict:
    """
    Mode B: Compare two runs of the same batch.
    Looks for samples analyzed twice (same SampleID, different Instrument or timestamps).
    Computes RPD between runs and flags drifts.
    """
    if log is not None:
        log.append("ℹ Mode B: Comparing instrumental runs...")
    
    results = {
        'n_compared': 0,
        'n_drift_detected': 0,
        'run_comparison': [],
        'elements_analyzed': []
    }
    
    # Check if we have Instrument column to distinguish runs
    if 'Instrument' not in df.columns:
        if log is not None:
            log.append("⚠ Mode B: No Instrument column found, skipping run comparison")
        return results
    
    # Group by SampleID and find duplicates (multiple runs)
    grouped = df.groupby('SampleID')
    
    elem_cols = [c for c in df.columns if c.endswith('_ppm')]
    
    for sample_id, group in grouped:
        if len(group) < 2:
            continue
        
        # We have multiple runs for this sample
        runs = group.to_dict('records')
        
        # Compare each pair of runs
        for i in range(len(runs)):
            for j in range(i+1, len(runs)):
                run1, run2 = runs[i], runs[j]
                
                comparison = {
                    'sample_id': sample_id,
                    'run1_instrument': run1.get('Instrument', 'Unknown'),
                    'run2_instrument': run2.get('Instrument', 'Unknown'),
                    'element_comparisons': []
                }
                
                for elem_col in elem_cols:
                    val1 = run1.get(elem_col)
                    val2 = run2.get(elem_col)
                    
                    if val1 is not None and val2 is not None and pd.notna(val1) and pd.notna(val2):
                        # Calculate RPD
                        mean_val = (val1 + val2) / 2
                        if mean_val > 0:
                            rpd = abs(val1 - val2) / mean_val * 100
                            
                            status = 'PASS'
                            if rpd > 20:
                                status = 'FAIL'
                            elif rpd > 10:
                                status = 'WARN'
                            
                            comparison['element_comparisons'].append({
                                'element': elem_col.replace('_ppm', ''),
                                'run1_value': val1,
                                'run2_value': val2,
                                'rpd': round(rpd, 2),
                                'status': status
                            })
                            
                            if status in ('WARN', 'FAIL'):
                                results['n_drift_detected'] += 1
                
                if comparison['element_comparisons']:
                    results['run_comparison'].append(comparison)
                    results['n_compared'] += 1
    
    # Extract unique elements analyzed
    all_elements = set()
    for comp in results['run_comparison']:
        for ec in comp['element_comparisons']:
            all_elements.add(ec['element'])
    results['elements_analyzed'] = sorted(list(all_elements))
    
    if log is not None:
        log.append(f"✓ Mode B: Compared {results['n_compared']} sample pairs across runs")
        if results['n_drift_detected'] > 0:
            log.append(f"⚠ Mode B: Detected {results['n_drift_detected']} potential drifts")
    
    return results
