"""mode_d_labos.py — Mode D implementation: compare two laboratories."""
import pandas as pd
import numpy as np
from typing import Dict, List

def run_mode_d(df: pd.DataFrame, log: list = None) -> dict:
    """
    Mode D: Compare results from two different laboratories.
    Looks for samples analyzed by multiple labs (same SampleID, different Lab or Method).
    Computes bias statistics and flags inter-lab discrepancies.
    """
    if log is not None:
        log.append("ℹ Mode D: Comparing laboratory results...")
    
    results = {
        'n_compared': 0,
        'n_bias_detected': 0,
        'lab_comparison': [],
        'laboratories': [],
        'elements_analyzed': []
    }
    
    # Check if we have Lab or Method column to distinguish labs
    id_col = None
    if 'Lab' in df.columns:
        id_col = 'Lab'
    elif 'Method' in df.columns:
        id_col = 'Method'
    elif 'Instrument' in df.columns:
        id_col = 'Instrument'
    
    if not id_col:
        if log is not None:
            log.append("⚠ Mode D: No Lab/Method/Instrument column found, skipping lab comparison")
        return results
    
    # Group by SampleID and find duplicates (multiple labs)
    grouped = df.groupby('SampleID')
    
    elem_cols = [c for c in df.columns if c.endswith('_ppm')]
    
    for sample_id, group in grouped:
        if len(group) < 2:
            continue
        
        # We have multiple lab results for this sample
        labs_data = group.to_dict('records')
        
        # Compare each pair of labs
        for i in range(len(labs_data)):
            for j in range(i+1, len(labs_data)):
                lab1, lab2 = labs_data[i], labs_data[j]
                
                lab1_name = lab1.get(id_col, 'Unknown')
                lab2_name = lab2.get(id_col, 'Unknown')
                
                # Skip if same lab
                if lab1_name == lab2_name:
                    continue
                
                comparison = {
                    'sample_id': sample_id,
                    'lab1_name': lab1_name,
                    'lab2_name': lab2_name,
                    'element_comparisons': []
                }
                
                for elem_col in elem_cols:
                    val1 = lab1.get(elem_col)
                    val2 = lab2.get(elem_col)
                    
                    if val1 is not None and val2 is not None and pd.notna(val1) and pd.notna(val2):
                        # Calculate relative bias
                        mean_val = (val1 + val2) / 2
                        if mean_val > 0:
                            bias = ((val2 - val1) / mean_val) * 100
                            abs_bias = abs(bias)
                            
                            status = 'PASS'
                            if abs_bias > 20:
                                status = 'FAIL'
                            elif abs_bias > 10:
                                status = 'WARN'
                            
                            comparison['element_comparisons'].append({
                                'element': elem_col.replace('_ppm', ''),
                                'lab1_value': val1,
                                'lab2_value': val2,
                                'bias_percent': round(bias, 2),
                                'abs_bias': round(abs_bias, 2),
                                'status': status
                            })
                            
                            if status in ('WARN', 'FAIL'):
                                results['n_bias_detected'] += 1
                
                if comparison['element_comparisons']:
                    results['lab_comparison'].append(comparison)
                    results['n_compared'] += 1
    
    # Extract unique laboratories
    all_labs = set()
    all_elements = set()
    for comp in results['lab_comparison']:
        all_labs.add(comp['lab1_name'])
        all_labs.add(comp['lab2_name'])
        for ec in comp['element_comparisons']:
            all_elements.add(ec['element'])
    
    results['laboratories'] = sorted(list(all_labs))
    results['elements_analyzed'] = sorted(list(all_elements))
    
    if log is not None:
        log.append(f"✓ Mode D: Compared {results['n_compared']} sample pairs across labs")
        if results['n_bias_detected'] > 0:
            log.append(f"⚠ Mode D: Detected {results['n_bias_detected']} inter-lab biases")
    
    return results
