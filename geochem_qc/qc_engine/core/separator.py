"""
separator.py — Sépare les QC TES (tes tests terrain) des QC LABO (contrôles internes du laboratoire).

Règles:
- QC_Source='TES' → BLK/STD/DUP de tes propres contrôles → validation principale
- QC_Source='LAB' ou IDs labo non-mappés → QC labo informatifs → section séparée
"""

import pandas as pd
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent.parent
MEMORY_PATH = BASE_DIR / "memory.json"


def load_memory() -> dict:
    try:
        import json
        with open(MEMORY_PATH) as f:
            return json.load(f)
    except Exception:
        return {}


def separate_qc(df: pd.DataFrame, log: list = None) -> dict:
    """
    Sépare le DataFrame en deux:
    - df_tes: QC avec QC_Source='TES' ou mappés via dispatch
    - df_lab: QC avec QC_Source='LAB' ou IDs labo non-mappés
    
    Returns:
        dict avec df_tes, df_lab, et stats
    """
    if log is None:
        log = []
    
    mem = load_memory()
    
    # Si colonne QC_Source existe
    if 'QC_Source' in df.columns:
        df_tes = df[df['QC_Source'] == 'TES'].copy()
        df_lab = df[df['QC_Source'] == 'LAB'].copy()
        
        # Ceux sans QC_Source sont considérés comme ORIG ou à déduire
        df_no_source = df[df['QC_Source'].isna() | (df['QC_Source'] == '')].copy()
        
        # Dans df_no_source, séparer ORIG vs QAQC non-sourcés
        if 'SampleType' in df_no_source.columns:
            df_orig = df_no_source[df_no_source['SampleType'] == 'ORIG'].copy()
            df_qaqc_unsource = df_no_source[df_no_source['SampleType'] == 'QAQC'].copy()
            
            # Les QAQC non-sourcés: essayer de déduire via IDBlk/IDStd
            # Si IDBlk ou IDStd est rempli → probablement TES
            has_crm = df_qaqc_unsource['IDBlk'].notna() | df_qaqc_unsource['IDStd'].notna()
            df_tes = pd.concat([df_tes, df_qaqc_unsource[has_crm]], ignore_index=True)
            df_lab = pd.concat([df_lab, df_qaqc_unsource[~has_crm]], ignore_index=True)
            
            df_tes = pd.concat([df_tes, df_orig], ignore_index=True)
        else:
            df_tes = pd.concat([df_tes, df_no_source], ignore_index=True)
    else:
        # Pas de colonne QC_Source → tout dans TES par défaut
        df_tes = df.copy()
        df_lab = pd.DataFrame(columns=df.columns)
    
    # Stats
    tes_blk = df_tes[(df_tes['SampleType'] == 'QAQC') & (df_tes.get('QAQCType') == 'BLK')] if 'QAQCType' in df_tes.columns else pd.DataFrame()
    tes_std = df_tes[(df_tes['SampleType'] == 'QAQC') & (df_tes.get('QAQCType') == 'STD')] if 'QAQCType' in df_tes.columns else pd.DataFrame()
    tes_dup = df_tes[(df_tes['SampleType'] == 'QAQC') & (df_tes.get('QAQCType') == 'DUP')] if 'QAQCType' in df_tes.columns else pd.DataFrame()
    
    lab_blk = df_lab[(df_lab['SampleType'] == 'QAQC') & (df_lab.get('QAQCType') == 'BLK')] if 'QAQCType' in df_lab.columns and not df_lab.empty else pd.DataFrame()
    lab_std = df_lab[(df_lab['SampleType'] == 'QAQC') & (df_lab.get('QAQCType') == 'STD')] if 'QAQCType' in df_lab.columns and not df_lab.empty else pd.DataFrame()
    lab_dup = df_lab[(df_lab['SampleType'] == 'QAQC') & (df_lab.get('QAQCType') == 'DUP')] if 'QAQCType' in df_lab.columns and not df_lab.empty else pd.DataFrame()
    
    log.append(f"✓ Séparation QC: {len(df_tes)} TES / {len(df_lab)} LAB")
    log.append(f"  TES: {len(tes_blk)} BLK, {len(tes_std)} STD, {len(tes_dup)} DUP")
    if not df_lab.empty:
        log.append(f"  LAB: {len(lab_blk)} BLK, {len(lab_std)} STD, {len(lab_dup)} DUP (informatif)")
    
    return {
        'df_tes': df_tes,
        'df_lab': df_lab,
        'stats': {
            'n_tes_total': len(df_tes),
            'n_tes_blk': len(tes_blk),
            'n_tes_std': len(tes_std),
            'n_tes_dup': len(tes_dup),
            'n_lab_total': len(df_lab),
            'n_lab_blk': len(lab_blk),
            'n_lab_std': len(lab_std),
            'n_lab_dup': len(lab_dup),
        }
    }


def identify_lab_qc(df: pd.DataFrame, dispatch_df: pd.DataFrame = None, log: list = None) -> pd.DataFrame:
    """
    Identifie les QC labo: échantillons qui n'ont pas de SM (SampleID mappé)
    et qui sont marqués comme QAQC mais sans correspondance dans le dispatch.
    
    Ces QC labo sont ceux que le laboratoire a analysés pour son propre contrôle,
    mais qui ne font pas partie de tes BLK/STD/DUP planifiés.
    """
    if log is None:
        log = []
    
    df_lab = df.copy()
    
    # Marquer comme LAB les QAQC sans SM mappé
    if 'SampleType' in df_lab.columns and 'QAQCType' in df_lab.columns:
        qaqc_mask = df_lab['SampleType'] == 'QAQC'
        
        # Si pas de dispatch, tous les QAQC sans IDBlk/IDStd sont LAB
        if dispatch_df is None:
            no_crm = df_lab['IDBlk'].isna() & df_lab['IDStd'].isna()
            df_lab.loc[qaqc_mask & no_crm, 'QC_Source'] = 'LAB'
            n_lab = (qaqc_mask & no_crm).sum()
            log.append(f"ℹ {n_lab} QAQC identifiés comme LAB (pas de CRM associé)")
        else:
            # Avec dispatch: les QAQC dont SampleID n'est pas dans dispatch sont LAB
            dispatch_ids = set(dispatch_df['SampleID'].astype(str)) if 'SampleID' in dispatch_df.columns else set()
            df_lab['_in_dispatch'] = df_lab['SampleID'].astype(str).isin(dispatch_ids)
            df_lab.loc[qaqc_mask & ~df_lab['_in_dispatch'], 'QC_Source'] = 'LAB'
            n_lab = (qaqc_mask & ~df_lab['_in_dispatch']).sum()
            df_lab.drop(columns=['_in_dispatch'], inplace=True)
            log.append(f"ℹ {n_lab} QAQC identifiés comme LAB (hors dispatch)")
    
    return df_lab
