"""
qc_lab_engine.py - Validation des QC fournis par le laboratoire externe

Ce module traite les échantillons identifiés comme QC_LABO (BLK, STD, DUP)
en les comparant aux valeurs certifiées de la base Oreas/CRM.
Il permet de vérifier la performance du laboratoire indépendant.
"""

import pandas as pd
import numpy as np
from typing import Dict, Any, Optional

def calc_rpd(a: float, b: float) -> Optional[float]:
    """Calcule le Relative Percent Difference."""
    if a is None or b is None:
        return None
    if pd.isna(a) or pd.isna(b):
        return None
    denom = (a + b) / 2
    if denom == 0:
        return None
    return abs(a - b) / denom * 100

def validate_lab_qc(df_lab: pd.DataFrame, crm_db: Dict, config: Dict) -> Dict[str, Any]:
    """
    Valide tous les QC du laboratoire.
    
    Args:
        df_lab: DataFrame contenant uniquement les QC du labo (Source='LAB')
        crm_db: Dictionnaire des valeurs certifiées (oreas_certified + custom)
        config: Configuration des seuils
        
    Returns:
        Dictionary containing stats, flags, and charts data for Lab QC
    """
    if df_lab.empty:
        return {"status": "NO_DATA", "message": "Aucun QC labo à analyser"}

    results = {
        "status": "OK",
        "summary": {},
        "blanks": [],
        "standards": [],
        "duplicates": [],
        "flags": []
    }

    # Séparer par type de QC
    blanks = df_lab[df_lab['QC_Type'] == 'BLK'].copy()
    standards = df_lab[df_lab['QC_Type'] == 'STD'].copy()
    duplicates = df_lab[df_lab['QC_Type'] == 'DUP'].copy()

    # --- 1. TRAITEMENT DES STANDARDS LABO ---
    if not standards.empty:
        std_results = []
        for _, row in standards.iterrows():
            crm_name = row.get('CRM_Name', '')
            if not crm_name:
                continue
            
            # Trouver le CRM dans la DB (case insensitive)
            crm_data = None
            for key, val in crm_db.items():
                if key.lower() == crm_name.lower():
                    crm_data = val
                    break
            
            if not crm_data:
                results["flags"].append(f"CRM '{crm_name}' non trouvé dans la base pour Lab QC")
                continue

            # Analyser chaque élément
            elements = config.get('elements_actifs', [])
            for el in elements:
                col_name = f"{el}_ppm"
                if col_name not in row:
                    continue
                
                measured_val = row[col_name]
                if pd.isna(measured_val):
                    continue

                # Récupérer valeur certifiée (simplifié: prend la méthode par défaut ou première dispo)
                method = config.get('methode_defaut', '4-acides')
                certified_val = None
                tolerance = None
                
                if 'methods' in crm_data:
                    if method in crm_data['methods']:
                        if el in crm_data['methods'][method]:
                            certified_val = crm_data['methods'][method][el].get('certified')
                            sd = crm_data['methods'][method][el].get('1SD', 0)
                            tolerance = sd * 3 if sd else certified_val * 0.1
                    # Fallback première méthode si défaut pas trouvé
                    if certified_val is None:
                        for m_data in crm_data['methods'].values():
                            if el in m_data:
                                certified_val = m_data[el].get('certified')
                                sd = m_data[el].get('1SD', 0)
                                tolerance = sd * 3 if sd else certified_val * 0.1
                                break
                
                if certified_val is None:
                    continue

                # Calcul Recovery %
                recovery = (measured_val / certified_val) * 100 if certified_val != 0 else 0
                
                # Z-Score
                z_score = abs(measured_val - certified_val) / tolerance if tolerance else 0
                
                status = "PASS"
                if z_score > 3:
                    status = "FAIL"
                elif z_score > 2:
                    status = "WARN"
                
                std_results.append({
                    "SampleID": row['SampleID'],
                    "CRM": crm_name,
                    "Element": el,
                    "Measured": measured_val,
                    "Certified": certified_val,
                    "Recovery_%": round(recovery, 1),
                    "Z_Score": round(z_score, 2),
                    "Status": status
                })
                
                if status != "PASS":
                    results["flags"].append(f"Lab Std {row['SampleID']} ({el}): {status} (Rec: {recovery:.1f}%)")

        results["standards"] = pd.DataFrame(std_results) if std_results else pd.DataFrame()

    # --- 2. TRAITEMENT DES BLANCS LABO ---
    if not blanks.empty:
        blk_results = []
        # On considère que les blancs ne doivent rien avoir. 
        # Si le labo utilise un blanc certifié (rare), on pourrait comparer, 
        # mais ici on vérifie juste la contamination > LOD estimé.
        
        # Estimation LOD basé sur tous les blancs du batch (labo + nous si mélangé, ici juste labo)
        elements = config.get('elements_actifs', [])
        for el in elements:
            col_name = f"{el}_ppm"
            vals = blanks[col_name].dropna()
            if len(vals) == 0:
                continue
            
            mean_val = vals.mean()
            std_val = vals.std()
            limit_detection = mean_val + (3 * std_val) if std_val > 0 else mean_val * 2
            
            # Flag si un blanc individuel dépasse largement la moyenne
            for _, row in blanks.iterrows():
                val = row[col_name]
                if pd.isna(val):
                    continue
                if val > limit_detection * 2: # Seuil arbitraire de contamination forte
                     results["flags"].append(f"Contamination Labo suspectée sur Blanc {row['SampleID']} ({el}: {val})")

        results["blanks"] = blanks  # On retourne le DF brut pour affichage

    # --- 3. TRAITEMENT DES DUPLICATES LABO ---
    if not duplicates.empty:
        dup_results = []
        # Appairage simple par IDParent ou par nom similaire
        # Ici on suppose que le parser a déjà mis un IDParent ou on groupe par paire proche
        # Pour simplifier, on cherche les paires explicites
        pairs = duplicates.groupby('ID_Parent')
        
        for parent_id, group in pairs:
            if len(group) < 2:
                continue
            
            # Prendre les deux premiers
            s1 = group.iloc[0]
            s2 = group.iloc[1]
            
            elements = config.get('elements_actifs', [])
            for el in elements:
                c1 = s1.get(f"{el}_ppm")
                c2 = s2.get(f"{el}_ppm")
                
                if pd.isna(c1) or pd.isna(c2) or (c1 == 0 and c2 == 0):
                    continue
                
                rpd = calc_rpd(c1, c2)
                limit_rpd = config.get('seuils', {}).get('rpd_fail', 20)
                
                status = "PASS"
                if rpd > limit_rpd:
                    status = "FAIL"
                    results["flags"].append(f"Lab Dup {parent_id} ({el}): RPD {rpd:.1f}% > {limit_rpd}%")
                elif rpd > (limit_rpd * 0.7):
                    status = "WARN"
                
                dup_results.append({
                    "Pair_ID": parent_id,
                    "Element": el,
                    "Val_1": float(c1),
                    "Val_2": float(c2),
                    "RPD_%": round(rpd, 1),
                    "Status": status
                })
        
        results["duplicates"] = pd.DataFrame(dup_results) if dup_results else pd.DataFrame()

    # Résumé
    total_flags = len(results["flags"])
    if total_flags == 0:
        results["summary"]["text"] = "QC Labo : Conformité parfaite détectée."
        results["summary"]["status"] = "PASS"
    elif total_flags < 3:
        results["summary"]["text"] = f"QC Labo : {total_flags} anomalies mineures."
        results["summary"]["status"] = "WARN"
    else:
        results["summary"]["text"] = f"QC Labo : {total_flags} problèmes détectés. Prudence."
        results["summary"]["status"] = "FAIL"

    return results
