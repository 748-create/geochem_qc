"""
pdf_writer.py — ReportLab PDF report: Summary + Charts + Anomalies.
"""

import io
import json
from pathlib import Path
from datetime import datetime

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.units import cm
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, Table,
                                 TableStyle, Image, PageBreak, HRFlowable,
                                 KeepTogether)
from reportlab.lib.colors import HexColor, white, black

BASE_DIR = Path(__file__).parent.parent.parent
CONFIG_PATH = BASE_DIR / "config.json"

# Design colors
C_NAVY    = HexColor('#1B2A4A')
C_GREEN   = HexColor('#2D7D46')
C_ORANGE  = HexColor('#E65100')
C_CYAN    = HexColor('#0097A7')
C_PASS    = HexColor('#C8E6C9')
C_WARN    = HexColor('#FFF9C4')
C_FAIL    = HexColor('#FFCDD2')
C_GRAY    = HexColor('#F5F5F5')
C_DKGRAY  = HexColor('#424242')


def load_config() -> dict:
    with open(CONFIG_PATH) as f:
        return json.load(f)


def _status_color(status: str):
    return {'PASS': C_PASS, 'WARN': C_WARN, 'FAIL': C_FAIL}.get(status, C_GRAY)


def _fig_to_image(fig, width_cm=16, height_cm=8) -> Image:
    buf = io.BytesIO()
    fig.savefig(buf, format='png', dpi=150, bbox_inches='tight',
                facecolor='white', edgecolor='none')
    buf.seek(0)
    plt.close(fig)
    img = Image(buf)
    img.drawWidth = width_cm * cm
    img.drawHeight = height_cm * cm
    return img


def _make_summary_chart(df: pd.DataFrame, elements: list) -> plt.Figure:
    """Box plot of priority elements in ORIG samples."""
    orig = df[df['SampleType'] == 'ORIG']
    data = []
    labels = []
    for el in elements:
        col = f'{el}_ppm'
        if col in orig.columns:
            vals = orig[col].dropna().values
            if len(vals) > 0:
                data.append(vals)
                labels.append(el)

    if not data:
        return None

    fig, ax = plt.subplots(figsize=(10, 5))
    bp = ax.boxplot(data, patch_artist=True, notch=False,
                    medianprops=dict(color='#E65100', linewidth=2))
    colors_list = ['#1B2A4A', '#2D7D46', '#0097A7', '#6A1B9A']
    for patch, color in zip(bp['boxes'], colors_list * 10):
        patch.set_facecolor(color)
        patch.set_alpha(0.7)

    ax.set_xticklabels(labels, fontsize=11)
    ax.set_ylabel('Concentration (ppm)', fontsize=10)
    ax.set_title('Priority Elements — Distribution in ORIG Samples', fontsize=12, fontweight='bold')
    ax.set_facecolor('#FAFAFA')
    fig.patch.set_facecolor('white')
    fig.tight_layout()
    return fig


def _make_qc_summary_chart(qc_results: dict) -> plt.Figure:
    """Donut chart of QC status overview."""
    categories = []
    counts = []
    chart_colors = []

    dup = qc_results.get('duplicates', {})
    if dup.get('n_pairs', 0) > 0:
        categories += ['DUP PASS', 'DUP WARN', 'DUP FAIL']
        counts += [dup.get('n_pass', 0), dup.get('n_warn', 0), dup.get('n_fail', 0)]
        chart_colors += ['#C8E6C9', '#FFF9C4', '#FFCDD2']

    blk = qc_results.get('blanks', {})
    for crm, res in blk.get('crm_results', {}).items():
        st = res.get('overall_status', 'PASS')
        categories.append(f'BLK {crm}')
        counts.append(1)
        chart_colors.append('#C8E6C9' if st == 'PASS' else '#FFF9C4' if st == 'WARN' else '#FFCDD2')

    std = qc_results.get('standards', {})
    for crm, res in std.get('crm_results', {}).items():
        st = res.get('overall_status', 'PASS')
        categories.append(f'STD {crm}')
        counts.append(1)
        chart_colors.append('#C8E6C9' if st == 'PASS' else '#FFF9C4' if st == 'WARN' else '#FFCDD2')

    if not counts or sum(counts) == 0:
        return None

    fig, ax = plt.subplots(figsize=(8, 6))
    wedges, texts, autotexts = ax.pie(
        counts, labels=categories, colors=chart_colors,
        autopct='%1.0f%%', startangle=90,
        wedgeprops=dict(width=0.6, edgecolor='white', linewidth=1.5)
    )
    for t in texts:
        t.set_fontsize(9)
    for at in autotexts:
        at.set_fontsize(8)

    ax.set_title('QC Overview', fontsize=12, fontweight='bold')
    fig.patch.set_facecolor('white')
    fig.tight_layout()
    return fig


def write_pdf(df: pd.DataFrame, qc_results: dict, completeness: dict,
              mode: str, output_dir: Path, zone: str = None) -> str:
    """Write PDF report. Returns filepath."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    cfg = load_config()
    now_str = datetime.now().strftime('%Y-%m-%d %H:%M')
    now_file = datetime.now().strftime('%Y%m%d_%H%M%S')
    zone_str = zone or 'ZONE'
    elem_prio = cfg.get('elements_prioritaires', ['Ta', 'Nb', 'Ti', 'Rb'])

    filename = f"GeoQC_{zone_str}_Mode{mode}_{now_file}.pdf"
    filepath = output_dir / filename

    doc = SimpleDocTemplate(
        str(filepath), pagesize=A4,
        topMargin=1.5*cm, bottomMargin=1.5*cm,
        leftMargin=1.8*cm, rightMargin=1.8*cm
    )

    styles = getSampleStyleSheet()
    style_h1 = ParagraphStyle('h1', parent=styles['Heading1'],
                               textColor=C_NAVY, fontSize=16, spaceAfter=6)
    style_h2 = ParagraphStyle('h2', parent=styles['Heading2'],
                               textColor=C_NAVY, fontSize=12, spaceAfter=4)
    style_body = ParagraphStyle('body', parent=styles['Normal'],
                                fontSize=10, spaceAfter=3)
    style_warn = ParagraphStyle('warn', parent=styles['Normal'],
                                fontSize=10, textColor=C_ORANGE, spaceAfter=3)

    story = []

    # ── PAGE 1: Summary ──────────────────────────────────────────────────────
    story.append(Paragraph('GeoQC Pro — Geochemical QC Report', style_h1))
    story.append(Paragraph(f'Zone: {zone_str} | Mode: {mode} | Generated: {now_str}', style_body))
    story.append(HRFlowable(width='100%', thickness=2, color=C_NAVY))
    story.append(Spacer(1, 0.3*cm))

    # QC Score
    score = completeness.get('score', 'N/A')
    score_color = C_PASS if score == 'COMPLET' else C_WARN if score in ('PARTIEL', 'ASYMÉTRIQUE') else C_FAIL
    story.append(Paragraph(f'QC Completeness Score: <b>{score}</b>', style_h2))

    # Counts table
    counts_data = [
        ['Category', 'Count', 'Rate'],
        ['ORIG samples', completeness.get('n_orig', 0), '—'],
        ['Duplicates (TES)', completeness.get('n_dup', 0), f"{completeness.get('dup_rate', 0):.1f}%"],
        ['Blanks (TES)', completeness.get('n_blk', 0), f"{completeness.get('blk_rate', 0):.1f}%"],
        ['Standards (TES)', completeness.get('n_std', 0), f"{completeness.get('std_rate', 0):.1f}%"],
        ['Lab QC (info)', completeness.get('n_lab_qc', 0), 'Informational'],
    ]
    t = Table(counts_data, colWidths=[7*cm, 4*cm, 4*cm])
    t.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), C_NAVY),
        ('TEXTCOLOR', (0,0), (-1,0), white),
        ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
        ('FONTSIZE', (0,0), (-1,-1), 10),
        ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.white, C_GRAY]),
        ('GRID', (0,0), (-1,-1), 0.5, colors.grey),
        ('ALIGN', (1,0), (-1,-1), 'CENTER'),
    ]))
    story.append(t)
    story.append(Spacer(1, 0.3*cm))

    # QC Results summary table
    story.append(Paragraph('QC Results Summary', style_h2))
    qc_rows = [['QC Type', 'N', 'PASS', 'WARN', 'FAIL', 'Overall']]

    dup_r = qc_results.get('duplicates', {})
    if dup_r.get('n_pairs', 0) > 0:
        qc_rows.append(['Duplicates', dup_r.get('n_pairs', 0),
                        dup_r.get('n_pass', 0), dup_r.get('n_warn', 0),
                        dup_r.get('n_fail', 0),
                        'FAIL' if dup_r.get('n_fail', 0) > 0 else
                        'WARN' if dup_r.get('n_warn', 0) > 0 else 'PASS'])

    for crm, res in qc_results.get('blanks', {}).get('crm_results', {}).items():
        qc_rows.append([f'Blank — {crm}', res.get('n_measurements', 0),
                        '—', '—', '—', res.get('overall_status', 'PASS')])

    for crm, res in qc_results.get('standards', {}).get('crm_results', {}).items():
        qc_rows.append([f'Standard — {crm}', res.get('n_measurements', 0),
                        '—', '—', '—', res.get('overall_status', 'PASS')])

    if len(qc_rows) > 1:
        qt = Table(qc_rows, colWidths=[5.5*cm, 2*cm, 2*cm, 2*cm, 2*cm, 3*cm])
        ts_cmds = [
            ('BACKGROUND', (0,0), (-1,0), C_NAVY),
            ('TEXTCOLOR', (0,0), (-1,0), white),
            ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
            ('FONTSIZE', (0,0), (-1,-1), 9),
            ('GRID', (0,0), (-1,-1), 0.5, colors.grey),
            ('ALIGN', (1,0), (-1,-1), 'CENTER'),
        ]
        for ri, row in enumerate(qc_rows[1:], start=1):
            st = row[-1]
            bg = C_PASS if st == 'PASS' else C_WARN if st == 'WARN' else C_FAIL
            ts_cmds.append(('BACKGROUND', (-1, ri), (-1, ri), bg))
        qt.setStyle(TableStyle(ts_cmds))
        story.append(qt)

    # QC pie chart
    qc_fig = _make_qc_summary_chart(qc_results)
    if qc_fig:
        story.append(Spacer(1, 0.5*cm))
        story.append(_fig_to_image(qc_fig, width_cm=12, height_cm=8))

    # ── PAGE 2: Element distributions ────────────────────────────────────────
    story.append(PageBreak())
    story.append(Paragraph('Element Distributions — ORIG Samples', style_h1))
    story.append(HRFlowable(width='100%', thickness=2, color=C_NAVY))
    story.append(Spacer(1, 0.3*cm))

    summary_fig = _make_summary_chart(df, elem_prio)
    if summary_fig:
        story.append(_fig_to_image(summary_fig, width_cm=16, height_cm=8))

    # Stats table per element
    orig = df[df['SampleType'] == 'ORIG']
    stats_rows = [['Element', 'N', 'Min', 'Mean', 'Median', 'P90', 'Max', 'Std Dev']]
    for el in elem_prio:
        col = f'{el}_ppm'
        if col not in orig.columns:
            continue
        vals = orig[col].dropna()
        if len(vals) == 0:
            continue
        stats_rows.append([
            el, len(vals),
            f'{vals.min():.3f}', f'{vals.mean():.3f}',
            f'{vals.median():.3f}', f'{vals.quantile(0.9):.3f}',
            f'{vals.max():.3f}', f'{vals.std():.3f}'
        ])

    if len(stats_rows) > 1:
        st_table = Table(stats_rows, colWidths=[2*cm]*8)
        st_table.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,0), C_NAVY),
            ('TEXTCOLOR', (0,0), (-1,0), white),
            ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
            ('FONTSIZE', (0,0), (-1,-1), 9),
            ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.white, C_GRAY]),
            ('GRID', (0,0), (-1,-1), 0.5, colors.grey),
            ('ALIGN', (1,0), (-1,-1), 'CENTER'),
        ]))
        story.append(Spacer(1, 0.3*cm))
        story.append(st_table)

    # ── PAGE 3: Anomalies (if any WARN or FAIL) ───────────────────────────────
    has_issues = (dup_r.get('n_fail', 0) > 0 or dup_r.get('n_warn', 0) > 0 or
                  any(r.get('overall_status') in ('WARN', 'FAIL')
                      for r in qc_results.get('blanks', {}).get('crm_results', {}).values()) or
                  any(r.get('overall_status') in ('WARN', 'FAIL')
                      for r in qc_results.get('standards', {}).get('crm_results', {}).values()))

    if has_issues:
        story.append(PageBreak())
        story.append(Paragraph('Anomalies & Recommendations', style_h1))
        story.append(HRFlowable(width='100%', thickness=2, color=C_ORANGE))
        story.append(Spacer(1, 0.3*cm))

        # Duplicate failures
        for pair in dup_r.get('pairs', []):
            if pair.get('overall_status') in ('WARN', 'FAIL'):
                el_issues = [f"{el}: RPD={rv['rpd']}% ({rv['status']})"
                             for el, rv in pair.get('rpd_values', {}).items()
                             if rv.get('status') in ('WARN', 'FAIL')]
                if el_issues:
                    story.append(Paragraph(
                        f"⚠ DUP {pair['dup_id']} vs {pair['parent_id']}: {' | '.join(el_issues)}",
                        style_warn))

        # Blank issues
        for crm, res in qc_results.get('blanks', {}).get('crm_results', {}).items():
            for el, er in res.get('elements', {}).items():
                for issue in er.get('issues', []):
                    story.append(Paragraph(f"⚠ BLK {crm} — {el}: {issue}", style_warn))

        # Standard issues
        for crm, res in qc_results.get('standards', {}).get('crm_results', {}).items():
            for el, er in res.get('elements', {}).items():
                for issue in er.get('issues', []):
                    story.append(Paragraph(f"⚠ STD {crm} — {el}: {issue}", style_warn))

        # Recommendations
        story.append(Spacer(1, 0.5*cm))
        story.append(Paragraph('Recommendations', style_h2))
        if dup_r.get('n_fail', 0) > 0:
            fail_ids = [p['dup_id'] for p in dup_r.get('pairs', [])
                        if p.get('overall_status') == 'FAIL']
            story.append(Paragraph(
                f"→ Re-analyze duplicate pairs: {', '.join(fail_ids[:5])}", style_body))

    story.append(Spacer(1, 0.5*cm))
    story.append(Paragraph(
        f'Report generated by GeoQC Pro | {now_str}',
        ParagraphStyle('footer', parent=styles['Normal'], fontSize=8,
                       textColor=C_DKGRAY, alignment=1)))

    doc.build(story)
    return str(filepath)
