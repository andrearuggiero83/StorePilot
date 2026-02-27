from __future__ import annotations

from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    import matplotlib.pyplot as plt
except Exception:  # pragma: no cover - optional dependency
    plt = None  # type: ignore
from openpyxl import Workbook
from openpyxl.drawing.image import Image as XLImage
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.lib.utils import ImageReader
from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle


def _fit_within(src_w: float, src_h: float, max_w: float, max_h: float) -> Tuple[float, float]:
    if src_w <= 0 or src_h <= 0:
        return max_w, max_h
    scale = min(max_w / src_w, max_h / src_h)
    return src_w * scale, src_h * scale


def _logo_size(logo_path: str, max_w: float, max_h: float) -> Tuple[float, float]:
    try:
        w, h = ImageReader(logo_path).getSize()
        return _fit_within(float(w), float(h), float(max_w), float(max_h))
    except Exception:
        return max_w, max_h


def _pdf_page_bg(canvas_obj, doc_obj) -> None:
    w, h = A4
    canvas_obj.saveState()
    canvas_obj.setFillColor(colors.white)
    canvas_obj.rect(0, 0, w, h, stroke=0, fill=1)
    canvas_obj.restoreState()


def _n(v: Any, default: float = 0.0) -> float:
    try:
        if v is None:
            return float(default)
        return float(v)
    except Exception:
        return float(default)


def _fmt_eur(v: Any) -> str:
    x = _n(v, float("nan"))
    if x != x:  # NaN
        return "n/a"
    return f"{x:,.0f} €".replace(",", "X").replace(".", ",").replace("X", ".")


def _fmt_pct(v: Any) -> str:
    if v is None:
        return "n/a"
    return f"{_n(v) * 100:.1f}%"


def _fmt_num(v: Any) -> str:
    if v is None:
        return "n/a"
    return f"{_n(v):,.1f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _lang(inputs: Optional[Dict[str, Any]] = None, explicit_lang: Optional[str] = None) -> str:
    if explicit_lang:
        s = str(explicit_lang).upper().strip()
        return "EN" if s == "EN" else "IT"
    if inputs:
        s = str(inputs.get("language", "IT")).upper().strip()
        return "EN" if s == "EN" else "IT"
    return "IT"


def _tr(lang: str, key: str) -> str:
    it = {
        "title": "StorePilot - Report economico professionale",
        "subtitle": "Riepilogo operativo, conto economico (P&L) e valutazione finale",
        "summary": "Executive summary",
        "pnl": "Conto economico (P&L) run-rate",
        "y1": "Vista Y1 (stagionalita + avviamento)",
        "invest": "Investimenti e ritorni",
        "assessment": "Valutazione finale",
        "notes": "Note narrative",
        "line_item": "Voce",
        "value": "Valore",
        "margin": "% su ricavi",
        "revenue": "Ricavi annui",
        "cogs": "COGS",
        "labor": "Personale",
        "prime_cost": "Prime Cost (COGS + Personale)",
        "opex": "OPEX",
        "marketing": "Marketing",
        "fee": "Fee",
        "occupancy": "Occupancy",
        "ebitda": "EBITDA",
        "be_rev": "Break-even (ricavi annui)",
        "be_orders": "Break-even (ordini/giorno)",
        "cash_inv": "Capitale investito",
        "roi_run": "ROI annuo (run-rate)",
        "payback_run": "Payback (mesi, run-rate)",
        "roi_y1": "ROI annuo (Y1)",
        "payback_y1": "Payback (mesi, Y1)",
        "status": "Esito",
        "business": "Tipologia locale",
        "open_days": "Giorni apertura/mese",
        "ramp": "Mesi avviamento",
        "generated": "Generato il",
        "kpi": "KPI",
        "disclaimer": "Disclaimer: le valutazioni presenti hanno esclusivo scopo illustrativo e non costituiscono consulenza finanziaria, legale o base sufficiente per decisioni di investimento.",
    }
    en = {
        "title": "StorePilot - Professional financial report",
        "subtitle": "Operational summary, P&L statement and final assessment",
        "summary": "Executive summary",
        "pnl": "Run-rate P&L statement",
        "y1": "Y1 view (seasonality + ramp-up)",
        "invest": "Investments and returns",
        "assessment": "Final assessment",
        "notes": "Narrative notes",
        "line_item": "Line item",
        "value": "Value",
        "margin": "% of revenue",
        "revenue": "Annual revenue",
        "cogs": "COGS",
        "labor": "Labor",
        "prime_cost": "Prime Cost (COGS + Labor)",
        "opex": "OPEX",
        "marketing": "Marketing",
        "fee": "Fee",
        "occupancy": "Occupancy",
        "ebitda": "EBITDA",
        "be_rev": "Break-even (annual revenue)",
        "be_orders": "Break-even (orders/day)",
        "cash_inv": "Cash invested",
        "roi_run": "Annual ROI (run-rate)",
        "payback_run": "Payback (months, run-rate)",
        "roi_y1": "Annual ROI (Y1)",
        "payback_y1": "Payback (months, Y1)",
        "status": "Outcome",
        "business": "Business type",
        "open_days": "Open days/month",
        "ramp": "Ramp-up months",
        "generated": "Generated on",
        "kpi": "KPI",
        "disclaimer": "Disclaimer: these estimates are for illustrative purposes only and do not constitute financial or legal advice, nor a sufficient basis for investment decisions.",
    }
    return (en if lang == "EN" else it).get(key, key)


def _be_orders(results: Dict[str, Any]) -> Any:
    v = results.get("break_even_orders_day")
    if v is None:
        v = results.get("break_even_orders_per_day")
    return v


def _status_and_messages(feasibility: Optional[Dict[str, Any]]) -> Tuple[str, List[str]]:
    if not isinstance(feasibility, dict):
        return "REVIEW", []
    raw = str(feasibility.get("status") or feasibility.get("label") or "REVIEW").upper()
    if raw == "NO_GO":
        raw = "NO GO"
    msgs = feasibility.get("reasons") or feasibility.get("messages") or feasibility.get("notes") or []
    if not isinstance(msgs, list):
        msgs = [str(msgs)]
    return raw, [str(m) for m in msgs if str(m).strip()]


def _narrative_notes(lang: str, results: Dict[str, Any], status: str) -> List[str]:
    rev = _n(results.get("revenue_annual_runrate"))
    ebitda = _n(results.get("ebitda_annual_runrate"))
    ebitda_pct = _n(results.get("ebitda_pct_annual_runrate"))
    prime = _n(results.get("prime_cost_pct"))
    occ = _n(results.get("occupancy_pct"))
    be = results.get("break_even_revenue_annual")

    if lang == "IT":
        notes = [
            f"Il modello stima ricavi annui run-rate pari a {_fmt_eur(rev)} con EBITDA {_fmt_eur(ebitda)} ({_fmt_pct(ebitda_pct)}).",
            f"Prime Cost al {_fmt_pct(prime)} e Occupancy al {_fmt_pct(occ)}.",
            f"Punto di pareggio stimato a {_fmt_eur(be)}.",
            f"Valutazione complessiva: {status}.",
        ]
    else:
        notes = [
            f"The model estimates run-rate annual revenue at {_fmt_eur(rev)} with EBITDA of {_fmt_eur(ebitda)} ({_fmt_pct(ebitda_pct)}).",
            f"Prime Cost at {_fmt_pct(prime)} and Occupancy at {_fmt_pct(occ)}.",
            f"Estimated break-even revenue at {_fmt_eur(be)}.",
            f"Overall assessment: {status}.",
        ]
    return notes


def _plot_break_even_png(results: Dict[str, Any], lang: str) -> Optional[bytes]:
    if plt is None:
        return None
    curve = results.get("break_even_curve")
    if not isinstance(curve, dict):
        return None

    xs = curve.get("revenue_annual", [])
    tc = curve.get("total_costs_annual", [])
    eb = curve.get("ebitda_annual", [])
    bex = curve.get("break_even_revenue_annual")
    if not xs or not tc or not eb:
        return None

    fig = plt.figure(figsize=(7.6, 3.9))
    ax = fig.add_subplot(111)
    ax.plot(xs, xs, color="#84665B", linewidth=2.2, label=_tr(lang, "revenue"))
    ax.plot(xs, tc, color="#B89581", linewidth=2.0, linestyle="--", label="Total costs" if lang == "EN" else "Costi totali")
    ax.plot(xs, eb, color="#1C1C1C", linewidth=2.2, label="EBITDA")
    ax.axhline(0, linewidth=0.8, color="#7A7A7A")
    if isinstance(bex, (int, float)) and bex > 0:
        ax.axvline(float(bex), color="#7A7A7A", linestyle=":", linewidth=1.5)
    ax.set_title("Break-even curve" if lang == "EN" else "Curva di break-even")
    ax.set_xlabel("€")
    ax.set_ylabel("€")
    ax.grid(alpha=0.15)
    ax.legend(loc="best")
    fig.tight_layout()
    bio = BytesIO()
    fig.savefig(bio, format="png", dpi=170)
    plt.close(fig)
    return bio.getvalue()


def _plot_pnl_png(results: Dict[str, Any], lang: str) -> Optional[bytes]:
    if plt is None:
        return None
    labels_it = ["Ricavi", "COGS", "Personale", "OPEX", "Marketing", "Fee", "Occupancy", "EBITDA"]
    labels_en = ["Revenue", "COGS", "Labor", "OPEX", "Marketing", "Fee", "Occupancy", "EBITDA"]
    labels = labels_en if lang == "EN" else labels_it
    vals = [
        _n(results.get("revenue_annual_runrate")),
        -_n(results.get("cogs_annual_runrate")),
        -_n(results.get("labor_annual_runrate")),
        -_n(results.get("opex_annual_runrate")),
        -_n(results.get("marketing_annual_runrate")),
        -_n(results.get("fee_annual_runrate")),
        -_n(results.get("occupancy_annual_runrate")),
        _n(results.get("ebitda_annual_runrate")),
    ]
    fig = plt.figure(figsize=(7.6, 3.9))
    ax = fig.add_subplot(111)
    colors_bar = ["#2E2E2E"] + ["#A64A4A"] * 6 + ["#1C1C1C"]
    ax.bar(labels, vals, color=colors_bar)
    ax.set_title("P&L bridge (run-rate)" if lang == "EN" else "Ponte P&L (run-rate)")
    ax.set_ylabel("€")
    ax.grid(axis="y", alpha=0.14)
    plt.xticks(rotation=15)
    fig.tight_layout()
    bio = BytesIO()
    fig.savefig(bio, format="png", dpi=170)
    plt.close(fig)
    return bio.getvalue()


def _plot_cost_mix_png(results: Dict[str, Any], lang: str) -> Optional[bytes]:
    if plt is None:
        return None
    rows = [
        ("COGS", _n(results.get("cogs_annual_runrate"))),
        ("Labor" if lang == "EN" else "Personale", _n(results.get("labor_annual_runrate"))),
        ("OPEX", _n(results.get("opex_annual_runrate"))),
        ("Marketing", _n(results.get("marketing_annual_runrate"))),
        ("Fee", _n(results.get("fee_annual_runrate"))),
        ("Occupancy", _n(results.get("occupancy_annual_runrate"))),
    ]
    rows = [(l, v) for (l, v) in rows if v > 0]
    if not rows:
        return None

    labels = [r[0] for r in rows]
    values = [r[1] for r in rows]
    colors_pie = ["#84665B", "#B89581", "#2E2E2E", "#A67F6F", "#8B817C", "#A64A4A"][: len(values)]
    fig = plt.figure(figsize=(7.6, 3.9))
    ax = fig.add_subplot(111)
    ax.pie(values, labels=labels, autopct="%1.1f%%", startangle=90, colors=colors_pie, wedgeprops={"linewidth": 1, "edgecolor": "white"})
    ax.set_title("Cost mix (annual)" if lang == "EN" else "Mix costi (annuo)")
    fig.tight_layout()
    bio = BytesIO()
    fig.savefig(bio, format="png", dpi=170)
    plt.close(fig)
    return bio.getvalue()


def _plot_daypart_png(inputs: Dict[str, Any], lang: str) -> Optional[bytes]:
    if plt is None:
        return None
    rows = inputs.get("daypart_breakdown") or []
    if not isinstance(rows, list):
        return None

    labels: List[str] = []
    values: List[float] = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        label = str(r.get("label", "") or "").strip()
        val = _n(r.get("monthly_revenue"))
        if label and val > 0:
            labels.append(label)
            values.append(val)
    if not labels:
        return None

    fig = plt.figure(figsize=(7.6, 3.9))
    ax = fig.add_subplot(111)
    ax.bar(labels, values, color="#84665B")
    ax.set_title("Daypart breakdown (monthly run-rate)" if lang == "EN" else "Breakdown per fascia (run-rate mensile)")
    ax.set_ylabel("€")
    ax.grid(axis="y", alpha=0.14)
    plt.xticks(rotation=15)
    fig.tight_layout()
    bio = BytesIO()
    fig.savefig(bio, format="png", dpi=170)
    plt.close(fig)
    return bio.getvalue()


def _pnl_rows(results: Dict[str, Any], y1: bool = False) -> List[Tuple[str, float, float]]:
    rev = _n(results.get("revenue_annual_y1")) if y1 else _n(results.get("revenue_annual_runrate"))
    cogs = _n(results.get("cogs_annual_y1")) if y1 else _n(results.get("cogs_annual_runrate"))
    labor = _n(results.get("labor_annual_y1")) if y1 else _n(results.get("labor_annual_runrate"))
    opex = _n(results.get("opex_annual_y1")) if y1 else _n(results.get("opex_annual_runrate"))
    mkt = _n(results.get("marketing_annual_y1")) if y1 else _n(results.get("marketing_annual_runrate"))
    fee = _n(results.get("fee_annual_y1")) if y1 else _n(results.get("fee_annual_runrate"))
    occ = _n(results.get("occupancy_annual_y1")) if y1 else _n(results.get("occupancy_annual_runrate"))
    ebitda = _n(results.get("ebitda_annual_y1")) if y1 else _n(results.get("ebitda_annual_runrate"))
    prime = cogs + labor

    def m(v: float) -> float:
        return (v / rev) if rev > 0 else 0.0

    return [
        ("revenue", rev, 1.0 if rev > 0 else 0.0),
        ("cogs", -cogs, -m(cogs)),
        ("labor", -labor, -m(labor)),
        ("prime_cost", -prime, -m(prime)),
        ("opex", -opex, -m(opex)),
        ("marketing", -mkt, -m(mkt)),
        ("fee", -fee, -m(fee)),
        ("occupancy", -occ, -m(occ)),
        ("ebitda", ebitda, m(ebitda)),
    ]


def build_excel_report_bytes(
    *,
    inputs: Optional[Dict[str, Any]] = None,
    results: Optional[Dict[str, Any]] = None,
    feasibility: Optional[Dict[str, Any]] = None,
    lang: Optional[str] = None,
    logo_path: str = "assets/logo.png",
) -> bytes:
    inputs = inputs or {}
    results = results or {}
    lang = _lang(inputs, lang)
    status, reasons = _status_and_messages(feasibility)
    notes = _narrative_notes(lang, results, status)

    wb = Workbook()
    ws = wb.active
    ws.title = "Executive"

    for idx, w in enumerate([42, 20, 20, 20, 20, 20], start=1):
        ws.column_dimensions[chr(64 + idx)].width = w

    thin = Side(style="thin", color="E6E8EC")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    title_font = Font(size=18, bold=True, color="1F2937")
    h_font = Font(size=12, bold=True, color="1F2937")
    k_font = Font(size=10, bold=True, color="374151")
    v_font = Font(size=11, color="111827")
    subtle_font = Font(size=9, color="6B7280")
    fill_head = PatternFill("solid", fgColor="F3F4F6")

    # Header banner
    ws.merge_cells("A1:F2")
    ws["A1"] = ""
    ws["A1"].fill = PatternFill("solid", fgColor="F5EFEA")
    for c in ("A", "B", "C", "D", "E", "F"):
        ws[f"{c}1"].fill = PatternFill("solid", fgColor="F5EFEA")
        ws[f"{c}2"].fill = PatternFill("solid", fgColor="F5EFEA")
    ws.row_dimensions[1].height = 34
    ws.row_dimensions[2].height = 34
    ws.row_dimensions[3].height = 16
    ws.row_dimensions[4].height = 16
    ws.row_dimensions[5].height = 24

    lp = Path(logo_path)
    if lp.exists():
        try:
            img = XLImage(str(lp))
            # Keep aspect ratio and allow a wider placement to avoid a tiny compressed logo.
            lw, lh = _logo_size(str(lp), max_w=520, max_h=120)
            img.width = int(lw)
            img.height = int(lh)
            ws.add_image(img, "A1")
        except Exception:
            pass

    ws.merge_cells("A5:F5")
    ws["A5"] = _tr(lang, "title")
    ws["A5"].font = Font(size=20, bold=True, color="1F2937")
    ws.merge_cells("A6:F6")
    ws["A6"] = f"{_tr(lang, 'subtitle')} - {_tr(lang, 'generated')} {datetime.now().strftime('%Y-%m-%d %H:%M')}"
    ws["A6"].font = subtle_font
    ws["A5"].alignment = Alignment(vertical="center")
    ws["A6"].alignment = Alignment(vertical="center")

    row = 8
    ws[f"A{row}"] = _tr(lang, "summary")
    ws[f"A{row}"].font = h_font
    row += 1

    summary_pairs = [
        (_tr(lang, "business"), str(inputs.get("business_label", "Custom"))),
        (_tr(lang, "open_days"), int(_n(inputs.get("open_days"), 30))),
        (_tr(lang, "revenue"), _fmt_eur(results.get("revenue_annual_runrate"))),
        (_tr(lang, "ebitda"), _fmt_eur(results.get("ebitda_annual_runrate"))),
        ("EBITDA %", _fmt_pct(results.get("ebitda_pct_annual_runrate"))),
        (_tr(lang, "be_rev"), _fmt_eur(results.get("break_even_revenue_annual"))),
        (_tr(lang, "be_orders"), _fmt_num(_be_orders(results))),
        (_tr(lang, "cash_inv"), _fmt_eur(results.get("cash_invested"))),
        (_tr(lang, "roi_run"), _fmt_pct(results.get("roi_annual"))),
        (_tr(lang, "payback_run"), _fmt_num(results.get("payback_months"))),
        (_tr(lang, "status"), status),
    ]
    for i, (k, v) in enumerate(summary_pairs):
        c1 = "A" if i % 2 == 0 else "D"
        c2 = "B" if i % 2 == 0 else "E"
        ws[f"{c1}{row}"] = k
        ws[f"{c1}{row}"].font = k_font
        ws[f"{c1}{row}"].fill = fill_head
        ws[f"{c1}{row}"].border = border
        ws[f"{c2}{row}"] = v
        ws[f"{c2}{row}"].font = v_font
        ws[f"{c2}{row}"].border = border
        ws[f"{c2}{row}"].alignment = Alignment(horizontal="right")
        if i % 2 == 1:
            row += 1
    row += 1

    ws[f"A{row}"] = _tr(lang, "pnl")
    ws[f"A{row}"].font = h_font
    row += 1
    ws[f"A{row}"], ws[f"B{row}"], ws[f"C{row}"] = _tr(lang, "line_item"), _tr(lang, "value"), _tr(lang, "margin")
    for c in ("A", "B", "C"):
        ws[f"{c}{row}"].font = k_font
        ws[f"{c}{row}"].fill = fill_head
        ws[f"{c}{row}"].border = border
    row += 1
    for k, val, pct in _pnl_rows(results, y1=False):
        ws[f"A{row}"] = _tr(lang, k)
        ws[f"B{row}"] = val
        ws[f"B{row}"].number_format = '#,##0 [$€-1];[Red]-#,##0 [$€-1]'
        ws[f"C{row}"] = pct
        ws[f"C{row}"].number_format = "0.0%"
        for c in ("A", "B", "C"):
            ws[f"{c}{row}"].border = border
        row += 1
    row += 1

    ws[f"A{row}"] = _tr(lang, "y1")
    ws[f"A{row}"].font = h_font
    row += 1
    for k, val, pct in _pnl_rows(results, y1=True):
        ws[f"A{row}"] = _tr(lang, k)
        ws[f"B{row}"] = val
        ws[f"B{row}"].number_format = '#,##0 [$€-1];[Red]-#,##0 [$€-1]'
        ws[f"C{row}"] = pct
        ws[f"C{row}"].number_format = "0.0%"
        for c in ("A", "B", "C"):
            ws[f"{c}{row}"].border = border
        row += 1
    row += 1

    ws[f"A{row}"] = _tr(lang, "invest")
    ws[f"A{row}"].font = h_font
    row += 1
    inv_rows = [
        (_tr(lang, "cash_inv"), _fmt_eur(results.get("cash_invested"))),
        (_tr(lang, "roi_run"), _fmt_pct(results.get("roi_annual"))),
        (_tr(lang, "payback_run"), _fmt_num(results.get("payback_months"))),
        (_tr(lang, "roi_y1"), _fmt_pct(results.get("roi_annual_y1"))),
        (_tr(lang, "payback_y1"), _fmt_num(results.get("payback_months_y1"))),
    ]
    for k, v in inv_rows:
        ws[f"A{row}"] = k
        ws[f"A{row}"].font = k_font
        ws[f"A{row}"].fill = fill_head
        ws[f"A{row}"].border = border
        ws[f"B{row}"] = v
        ws[f"B{row}"].font = v_font
        ws[f"B{row}"].border = border
        row += 1
    row += 1

    ws[f"A{row}"] = _tr(lang, "notes")
    ws[f"A{row}"].font = h_font
    row += 1
    for n in notes:
        ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=6)
        ws[f"A{row}"] = f"- {n}"
        ws[f"A{row}"].font = subtle_font
        ws[f"A{row}"].alignment = Alignment(wrap_text=True, vertical="top")
        row += 1
    row += 1

    ws[f"A{row}"] = _tr(lang, "assessment")
    ws[f"A{row}"].font = h_font
    row += 1
    ws[f"A{row}"] = f"{_tr(lang, 'status')}: {status}"
    ws[f"A{row}"].font = Font(size=11, bold=True, color="1F2937")
    row += 1
    for r in reasons:
        ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=6)
        ws[f"A{row}"] = f"- {r}"
        ws[f"A{row}"].font = subtle_font
        ws[f"A{row}"].alignment = Alignment(wrap_text=True, vertical="top")
        row += 1

    row += 1
    ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=6)
    ws[f"A{row}"] = _tr(lang, "disclaimer")
    ws[f"A{row}"].font = Font(size=8, italic=True, color="6B7280")
    ws[f"A{row}"].alignment = Alignment(wrap_text=True, vertical="top")

    ws2 = wb.create_sheet("Charts")
    ws2.column_dimensions["A"].width = 2
    ws2.column_dimensions["B"].width = 40
    ws2.column_dimensions["C"].width = 40
    ws2["B2"] = "All charts" if lang == "EN" else "Tutti i grafici"
    ws2["B2"].font = h_font
    try:
        be_png = _plot_break_even_png(results, lang)
    except Exception:
        be_png = None
    try:
        pnl_png = _plot_pnl_png(results, lang)
    except Exception:
        pnl_png = None
    try:
        cost_png = _plot_cost_mix_png(results, lang)
    except Exception:
        cost_png = None
    try:
        daypart_png = _plot_daypart_png(inputs, lang)
    except Exception:
        daypart_png = None
    if be_png:
        img1 = XLImage(BytesIO(be_png))
        img1.width = 620
        img1.height = 280
        ws2.add_image(img1, "B4")
    if pnl_png:
        img2 = XLImage(BytesIO(pnl_png))
        img2.width = 620
        img2.height = 280
        ws2.add_image(img2, "B22")
    if cost_png:
        img3 = XLImage(BytesIO(cost_png))
        img3.width = 620
        img3.height = 280
        ws2.add_image(img3, "B40")
    if daypart_png:
        img4 = XLImage(BytesIO(daypart_png))
        img4.width = 620
        img4.height = 280
        ws2.add_image(img4, "B58")

    ws.freeze_panes = "A9"

    bio = BytesIO()
    wb.save(bio)
    return bio.getvalue()


def build_pdf_report_bytes(
    *,
    inputs: Optional[Dict[str, Any]] = None,
    results: Optional[Dict[str, Any]] = None,
    feasibility: Optional[Dict[str, Any]] = None,
    lang: Optional[str] = None,
    logo_path: str = "assets/logo.png",
) -> bytes:
    inputs = inputs or {}
    results = results or {}
    lang = _lang(inputs, lang)
    status, reasons = _status_and_messages(feasibility)
    notes = _narrative_notes(lang, results, status)

    bio = BytesIO()
    doc = SimpleDocTemplate(
        bio,
        pagesize=A4,
        leftMargin=1.6 * cm,
        rightMargin=1.6 * cm,
        topMargin=1.4 * cm,
        bottomMargin=1.4 * cm,
    )
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle("title_sp", parent=styles["Heading1"], fontName="Helvetica-Bold", fontSize=16, leading=20, textColor=colors.HexColor("#1F2937"))
    h_style = ParagraphStyle("h_sp", parent=styles["Heading2"], fontName="Helvetica-Bold", fontSize=12, leading=15, spaceBefore=8, textColor=colors.HexColor("#1F2937"))
    body = ParagraphStyle("body_sp", parent=styles["BodyText"], fontName="Helvetica", fontSize=9.5, leading=13, textColor=colors.HexColor("#374151"))
    small = ParagraphStyle("small_sp", parent=styles["BodyText"], fontName="Helvetica", fontSize=8.8, leading=12, textColor=colors.HexColor("#4B5563"))

    story: List[Any] = []
    lp = Path(logo_path)
    if lp.exists():
        try:
            lw, lh = _logo_size(str(lp), max_w=9.4 * cm, max_h=3.0 * cm)
            story.append(Image(str(lp), width=lw, height=lh))
        except Exception:
            pass
    story.append(Spacer(1, 0.14 * cm))
    story.append(Paragraph(_tr(lang, "title"), title_style))
    story.append(Paragraph(f"{_tr(lang, 'subtitle')} - {_tr(lang, 'generated')} {datetime.now().strftime('%Y-%m-%d %H:%M')}", small))
    story.append(Spacer(1, 0.34 * cm))

    story.append(Paragraph(_tr(lang, "summary"), h_style))
    exec_data = [
        [_tr(lang, "kpi"), _tr(lang, "value"), _tr(lang, "kpi"), _tr(lang, "value")],
        [_tr(lang, "business"), str(inputs.get("business_label", "Custom")), _tr(lang, "open_days"), str(int(_n(inputs.get("open_days"), 30)))],
        [_tr(lang, "revenue"), _fmt_eur(results.get("revenue_annual_runrate")), _tr(lang, "ebitda"), _fmt_eur(results.get("ebitda_annual_runrate"))],
        ["EBITDA %", _fmt_pct(results.get("ebitda_pct_annual_runrate")), _tr(lang, "be_rev"), _fmt_eur(results.get("break_even_revenue_annual"))],
        [_tr(lang, "cash_inv"), _fmt_eur(results.get("cash_invested")), _tr(lang, "roi_run"), _fmt_pct(results.get("roi_annual"))],
        [_tr(lang, "payback_run"), _fmt_num(results.get("payback_months")), _tr(lang, "status"), status],
    ]
    t_exec = Table(exec_data, colWidths=[4.7 * cm, 3.1 * cm, 4.7 * cm, 3.1 * cm])
    t_exec.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#F3F4F6")),
        ("BACKGROUND", (0, 1), (-1, -1), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
        ("TEXTCOLOR", (0, 0), (-1, -1), colors.HexColor("#111827")),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#E5E7EB")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#FCFCFD")]),
    ]))
    story.append(t_exec)
    story.append(Spacer(1, 0.22 * cm))

    story.append(Paragraph(_tr(lang, "pnl"), h_style))
    pnl_data = [[_tr(lang, "line_item"), _tr(lang, "value"), _tr(lang, "margin")]]
    for k, v, p in _pnl_rows(results, y1=False):
        pnl_data.append([_tr(lang, k), _fmt_eur(v), _fmt_pct(p)])
    t_pnl = Table(pnl_data, colWidths=[7.8 * cm, 3.6 * cm, 3.2 * cm])
    t_pnl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#F3F4F6")),
        ("BACKGROUND", (0, 1), (-1, -1), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
        ("TEXTCOLOR", (0, 0), (-1, -1), colors.HexColor("#111827")),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#E5E7EB")),
        ("ALIGN", (1, 1), (-1, -1), "RIGHT"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#FCFCFD")]),
    ]))
    story.append(t_pnl)
    story.append(Spacer(1, 0.16 * cm))

    story.append(Paragraph(_tr(lang, "y1"), h_style))
    y1_data = [[_tr(lang, "line_item"), _tr(lang, "value"), _tr(lang, "margin")]]
    for k, v, p in _pnl_rows(results, y1=True):
        y1_data.append([_tr(lang, k), _fmt_eur(v), _fmt_pct(p)])
    t_y1 = Table(y1_data, colWidths=[7.8 * cm, 3.6 * cm, 3.2 * cm])
    t_y1.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#F3F4F6")),
        ("BACKGROUND", (0, 1), (-1, -1), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
        ("TEXTCOLOR", (0, 0), (-1, -1), colors.HexColor("#111827")),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#E5E7EB")),
        ("ALIGN", (1, 1), (-1, -1), "RIGHT"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#FCFCFD")]),
    ]))
    story.append(t_y1)
    story.append(Spacer(1, 0.16 * cm))

    story.append(Paragraph(_tr(lang, "invest"), h_style))
    inv_data = [
        [_tr(lang, "line_item"), _tr(lang, "value")],
        [_tr(lang, "cash_inv"), _fmt_eur(results.get("cash_invested"))],
        [_tr(lang, "roi_run"), _fmt_pct(results.get("roi_annual"))],
        [_tr(lang, "payback_run"), _fmt_num(results.get("payback_months"))],
        [_tr(lang, "roi_y1"), _fmt_pct(results.get("roi_annual_y1"))],
        [_tr(lang, "payback_y1"), _fmt_num(results.get("payback_months_y1"))],
    ]
    t_inv = Table(inv_data, colWidths=[7.8 * cm, 6.8 * cm])
    t_inv.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#F3F4F6")),
        ("BACKGROUND", (0, 1), (-1, -1), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
        ("TEXTCOLOR", (0, 0), (-1, -1), colors.HexColor("#111827")),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#E5E7EB")),
        ("ALIGN", (1, 1), (1, -1), "RIGHT"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#FCFCFD")]),
    ]))
    story.append(t_inv)
    story.append(Spacer(1, 0.16 * cm))

    story.append(Paragraph(_tr(lang, "notes"), h_style))
    for n in notes:
        story.append(Paragraph(f"- {n}", body))
    story.append(Spacer(1, 0.1 * cm))

    story.append(Paragraph(_tr(lang, "assessment"), h_style))
    story.append(Paragraph(f"<b>{_tr(lang, 'status')}:</b> {status}", body))
    for r in reasons:
        story.append(Paragraph(f"- {r}", body))
    story.append(Spacer(1, 0.22 * cm))

    try:
        be_png = _plot_break_even_png(results, lang)
    except Exception:
        be_png = None
    try:
        pnl_png = _plot_pnl_png(results, lang)
    except Exception:
        pnl_png = None
    try:
        cost_png = _plot_cost_mix_png(results, lang)
    except Exception:
        cost_png = None
    try:
        daypart_png = _plot_daypart_png(inputs, lang)
    except Exception:
        daypart_png = None

    chart_imgs = [be_png, pnl_png, cost_png, daypart_png]
    chart_imgs = [x for x in chart_imgs if x]
    for idx, img in enumerate(chart_imgs):
        story.append(Image(BytesIO(img), width=17.2 * cm, height=5.4 * cm))
        if idx < len(chart_imgs) - 1:
            story.append(Spacer(1, 0.12 * cm))

    story.append(Spacer(1, 0.18 * cm))
    story.append(Paragraph(f"<i>{_tr(lang, 'disclaimer')}</i>", small))

    doc.build(story, onFirstPage=_pdf_page_bg, onLaterPages=_pdf_page_bg)
    return bio.getvalue()
