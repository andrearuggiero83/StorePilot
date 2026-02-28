from __future__ import annotations

import base64
from datetime import datetime
import re
from uuid import uuid4

from typing import Dict, Any, List, Optional, Tuple

import inspect
from types import SimpleNamespace

import streamlit as st
import gspread
import yaml
import plotly.express as px
import plotly.graph_objects as go
from google.oauth2.service_account import Credentials

from engine.calculations import DaypartInput, calculate_financials
from engine.feasibility import evaluate_feasibility
from reports.report_builder import build_excel_report_bytes, build_pdf_report_bytes

TOOL_VERSION = "2026.02"
LEAD_SOURCE = "storepilot_report_section"


# ============================
# Page
# ============================
st.set_page_config(
    page_title="StorePilot",
    layout="wide",
    initial_sidebar_state="collapsed",
)


# ============================
# Helpers
# ============================
def load_yaml(path: str):
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _fmt_eur(x: Optional[float]) -> str:
    if x is None:
        return "n/a"
    try:
        return f"{float(x):,.0f} €".replace(",", "X").replace(".", ",").replace("X", ".")
    except Exception:
        return "n/a"


def _fmt_pct(x: Optional[float]) -> str:
    if x is None:
        return "n/a"
    try:
        return f"{float(x) * 100:.1f}%"
    except Exception:
        return "n/a"


# Embed image as base64 data URI for HTML
def _img_data_uri(path: str) -> str:
    """Return a data URI for a local image (PNG/JPG) to embed in HTML."""
    try:
        with open(path, "rb") as f:
            b = f.read()
        b64 = base64.b64encode(b).decode("utf-8")
        # Assume png if path endswith .png, else fallback to octet-stream
        mime = "image/png" if path.lower().endswith(".png") else "application/octet-stream"
        return f"data:{mime};base64,{b64}"
    except Exception:
        return ""

def _html_escape(s: str) -> str:
    return (
        str(s)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
    )

def _bench_range(bench: Dict[str, Any], k: str) -> Tuple[float, float, float]:
    obj = bench.get(k, {}) or {}
    mn = float(obj.get("min", 0.0))
    mx = float(obj.get("max", 1.0))
    dv = float(obj.get("default", mn))
    dv = max(mn, min(mx, dv))
    return mn, mx, dv

def _call_builder(
    func,
    *,
    inputs: Dict[str, Any],
    results: Dict[str, Any],
    feasibility: Dict[str, Any],
    lang: str,
) -> Tuple[Optional[bytes], Optional[str]]:
    """
    Chiama un builder (Excel/PDF) con matching best-effort della signature.
    Ritorna (bytes|None, error|None).
    """
    try:
        sig = inspect.signature(func)
        params = [p.name for p in sig.parameters.values() if p.name != "self"]

        aliases = {
            "inputs": ["inputs", "context", "payload_inputs", "params"],
            "results": ["results", "data", "payload", "model", "calc", "financials"],
            "feasibility": ["feasibility", "fe", "assessment", "evaluation"],
            "lang": ["lang", "language", "locale"],
        }

        kwargs: Dict[str, Any] = {}
        for name in params:
            if name in aliases["inputs"]:
                kwargs[name] = inputs
            elif name in aliases["results"]:
                kwargs[name] = results
            elif name in aliases["feasibility"]:
                kwargs[name] = feasibility
            elif name in aliases["lang"]:
                kwargs[name] = lang

        # 1) prova kwargs
        if kwargs:
            try:
                out = func(**kwargs)  # type: ignore
                return (out if isinstance(out, (bytes, bytearray)) else bytes(out), None)  # type: ignore
            except TypeError:
                pass

        # 2) fallback posizionali
        try:
            out = func(inputs, results, feasibility, lang)  # type: ignore
            return (out if isinstance(out, (bytes, bytearray)) else bytes(out), None)  # type: ignore
        except TypeError:
            try:
                out = func(inputs, results, feasibility)  # type: ignore
                return (out if isinstance(out, (bytes, bytearray)) else bytes(out), None)  # type: ignore
            except TypeError:
                try:
                    out = func(inputs, results)  # type: ignore
                    return (out if isinstance(out, (bytes, bytearray)) else bytes(out), None)  # type: ignore
                except TypeError:
                    out = func(results)  # type: ignore
                    return (out if isinstance(out, (bytes, bytearray)) else bytes(out), None)  # type: ignore

    except Exception as e:
        return None, str(e)


def _build_reports_cached(inputs: Dict[str, Any], results: Dict[str, Any], feasibility: Dict[str, Any], lang: str) -> Dict[str, Any]:
    xlsx_b, xlsx_err = _call_builder(
        build_excel_report_bytes,
        inputs=inputs,
        results=results,
        feasibility=feasibility,
        lang=lang,
    )
    pdf_b, pdf_err = _call_builder(
        build_pdf_report_bytes,
        inputs=inputs,
        results=results,
        feasibility=feasibility,
        lang=lang,
    )
    return {
        "xlsx_bytes": xlsx_b,
        "xlsx_err": xlsx_err,
        "pdf_bytes": pdf_b,
        "pdf_err": pdf_err,
    }
def _plotly_base_layout(fig: go.Figure) -> go.Figure:
    """Apply a consistent SaaS/tech layout aligned with the app theme.

    IMPORTANT UI RULE:
    - Titles are rendered OUTSIDE the chart (as section headings in Streamlit).
      Therefore Plotly figure titles MUST be disabled to avoid overlaps.
    """

    # Disable Plotly's internal title to prevent it from covering the plot.
    fig.update_layout(title_text="")

    fig.update_layout(
        margin=dict(l=34, r=22, t=34, b=36),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(
            family="Inter, system-ui, -apple-system, Segoe UI, Roboto, Arial",
            size=12,
            color="#1b1b1b",
        ),
        showlegend=True,
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.02,
            xanchor="left",
            x=0,
            title_text="",
        ),
    )

    fig.update_xaxes(showgrid=True, gridcolor="rgba(16,24,40,0.08)", zeroline=False)
    fig.update_yaxes(showgrid=True, gridcolor="rgba(16,24,40,0.08)", zeroline=False)

    return fig


# Backward-compatible alias (older code may call this name)
# Keep a single source of truth for chart styling.
def _sp_base_layout(fig: go.Figure) -> go.Figure:
    return _plotly_base_layout(fig)


def _placeholder_figure(title: str, *, kind: str = "line") -> go.Figure:
    """Professional placeholder chart (axes + baseline) when inputs are missing.

    Note: the chart title is rendered outside the figure in Streamlit, so we
    intentionally do NOT set a Plotly title.
    """

    lang = st.session_state.get("lang", "IT")
    note = "Inserisci dati per popolare il grafico" if lang == "IT" else "Enter inputs to populate the chart"

    if kind == "pie":
        fig = go.Figure(
            data=[
                go.Pie(
                    labels=["—"],
                    values=[1],
                    hole=0.62,
                    textinfo="none",
                    marker=dict(colors=["rgba(16,24,40,0.12)"]),
                    showlegend=False,
                )
            ]
        )
        fig.add_annotation(
            text=note,
            x=0.5,
            y=0.5,
            showarrow=False,
            font=dict(size=12, color="rgba(16,24,40,0.55)"),
        )
        return _plotly_base_layout(fig)

    x = list(range(1, 13))
    y = [0] * 12

    if kind == "bar":
        fig = go.Figure(
            data=[
                go.Bar(
                    x=x,
                    y=y,
                    marker=dict(color="rgba(16,24,40,0.18)"),
                    name="",
                    showlegend=False,
                )
            ]
        )
    else:
        fig = go.Figure(
            data=[
                go.Scatter(
                    x=x,
                    y=y,
                    mode="lines",
                    line=dict(color="rgba(16,24,40,0.24)", width=2),
                    name="",
                    showlegend=False,
                )
            ]
        )

    fig.add_annotation(
        text=note,
        x=0.5,
        y=0.5,
        xref="paper",
        yref="paper",
        showarrow=False,
        font=dict(size=12, color="rgba(16,24,40,0.55)"),
    )

    # For placeholders we don't want a legend at all.
    fig.update_layout(showlegend=False)

    return _plotly_base_layout(fig)


def label_business(bt: Dict[str, Any]) -> str:
    return bt.get("label_it") if st.session_state.get("lang", "IT") == "IT" else bt.get(
        "label_en", bt.get("label_it", bt.get("key", ""))
    )


def label_daypart(dp: Dict[str, Any]) -> str:
    return dp.get("label_it") if st.session_state.get("lang", "IT") == "IT" else dp.get(
        "label_en", dp.get("label_it", dp.get("key", ""))
    )


def icon(key: str) -> str:
    """Return a small inline SVG icon (HTML) for use inside unsafe_allow_html blocks."""
    icons = {
        "guide": """
<svg class=\"sp-ico\" viewBox=\"0 0 24 24\" fill=\"none\" xmlns=\"http://www.w3.org/2000/svg\">
  <path d=\"M4 19.5V6.6c0-.6.3-1.2.8-1.5C6 4.3 7.5 4 9 4h10v15H9c-1.5 0-3 .3-4.2 1.1-.5.3-.8.0-.8-.6z\" stroke=\"currentColor\" stroke-width=\"1.8\" stroke-linecap=\"round\" stroke-linejoin=\"round\"/>
  <path d=\"M9 4v15\" stroke=\"currentColor\" stroke-width=\"1.8\" stroke-linecap=\"round\"/>
</svg>""",
        "glossary": """
<svg class=\"sp-ico\" viewBox=\"0 0 24 24\" fill=\"none\" xmlns=\"http://www.w3.org/2000/svg\">
  <path d=\"M6 4h10a2 2 0 0 1 2 2v14H8a2 2 0 0 0-2 2V4z\" stroke=\"currentColor\" stroke-width=\"1.8\" stroke-linejoin=\"round\"/>
  <path d=\"M8 6h8M8 10h8M8 14h6\" stroke=\"currentColor\" stroke-width=\"1.8\" stroke-linecap=\"round\"/>
</svg>""",
        "setup": """
<svg class=\"sp-ico\" viewBox=\"0 0 24 24\" fill=\"none\" xmlns=\"http://www.w3.org/2000/svg\">
  <path d=\"M12 15.5a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7z\" stroke=\"currentColor\" stroke-width=\"1.8\"/>
  <path d=\"M19.4 15a8.2 8.2 0 0 0 .1-1l2-1.2-2-3.5-2.3.6a7.9 7.9 0 0 0-1.7-1l-.3-2.4H10.8l-.3 2.4a7.9 7.9 0 0 0-1.7 1L6.5 9.3 4.5 12.8l2 1.2a8.2 8.2 0 0 0 .1 1l-2 1.2 2 3.5 2.3-.6a7.9 7.9 0 0 0 1.7 1l.3 2.4h4.4l.3-2.4a7.9 7.9 0 0 0 1.7-1l2.3.6 2-3.5-2-1.2z\" stroke=\"currentColor\" stroke-width=\"1.2\" stroke-linecap=\"round\" stroke-linejoin=\"round\"/>
</svg>""",
        "dayparts": """
<svg class=\"sp-ico\" viewBox=\"0 0 24 24\" fill=\"none\" xmlns=\"http://www.w3.org/2000/svg\">
  <path d=\"M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18z\" stroke=\"currentColor\" stroke-width=\"1.8\"/>
  <path d=\"M12 7v5l3 2\" stroke=\"currentColor\" stroke-width=\"1.8\" stroke-linecap=\"round\" stroke-linejoin=\"round\"/>
</svg>""",
        "costs": """
<svg class=\"sp-ico\" viewBox=\"0 0 24 24\" fill=\"none\" xmlns=\"http://www.w3.org/2000/svg\">
  <path d=\"M7 7h14v14H7V7z\" stroke=\"currentColor\" stroke-width=\"1.8\"/>
  <path d=\"M3 3h14v4H3V3z\" stroke=\"currentColor\" stroke-width=\"1.8\"/>
  <path d=\"M10 11h8M10 15h8\" stroke=\"currentColor\" stroke-width=\"1.8\" stroke-linecap=\"round\"/>
</svg>""",
        "fee": """
<svg class=\"sp-ico\" viewBox=\"0 0 24 24\" fill=\"none\" xmlns=\"http://www.w3.org/2000/svg\">
  <path d=\"M20 12a8 8 0 1 1-2.3-5.7\" stroke=\"currentColor\" stroke-width=\"1.8\" stroke-linecap=\"round\"/>
  <path d=\"M20 4v6h-6\" stroke=\"currentColor\" stroke-width=\"1.8\" stroke-linecap=\"round\" stroke-linejoin=\"round\"/>
</svg>""",
        "occupancy": """
<svg class=\"sp-ico\" viewBox=\"0 0 24 24\" fill=\"none\" xmlns=\"http://www.w3.org/2000/svg\">
  <path d=\"M3 10.5 12 3l9 7.5V21H3V10.5z\" stroke=\"currentColor\" stroke-width=\"1.8\" stroke-linecap=\"round\" stroke-linejoin=\"round\"/>
  <path d=\"M9 21V13h6v8\" stroke=\"currentColor\" stroke-width=\"1.8\" stroke-linecap=\"round\" stroke-linejoin=\"round\"/>
</svg>""",
        "invest": """
<svg class=\"sp-ico\" viewBox=\"0 0 24 24\" fill=\"none\" xmlns=\"http://www.w3.org/2000/svg\">
  <path d=\"M4 7h16v4H4V7z\" stroke=\"currentColor\" stroke-width=\"1.8\"/>
  <path d=\"M7 11v10h10V11\" stroke=\"currentColor\" stroke-width=\"1.8\"/>
  <path d=\"M9 7V5a3 3 0 0 1 6 0v2\" stroke=\"currentColor\" stroke-width=\"1.8\" stroke-linecap=\"round\"/>
</svg>""",
        "seasonality": """
<svg class=\"sp-ico\" viewBox=\"0 0 24 24\" fill=\"none\" xmlns=\"http://www.w3.org/2000/svg\">
  <path d=\"M4 19V5\" stroke=\"currentColor\" stroke-width=\"1.8\" stroke-linecap=\"round\"/>
  <path d=\"M4 19h16\" stroke=\"currentColor\" stroke-width=\"1.8\" stroke-linecap=\"round\"/>
  <path d=\"M7 15l4-4 3 3 5-6\" stroke=\"currentColor\" stroke-width=\"1.8\" stroke-linecap=\"round\" stroke-linejoin=\"round\"/>
</svg>""",
        "fte": """
<svg class=\"sp-ico\" viewBox=\"0 0 24 24\" fill=\"none\" xmlns=\"http://www.w3.org/2000/svg\">
  <path d=\"M16 11a4 4 0 1 1-8 0 4 4 0 0 1 8 0z\" stroke=\"currentColor\" stroke-width=\"1.8\"/>
  <path d=\"M4 21a8 8 0 0 1 16 0\" stroke=\"currentColor\" stroke-width=\"1.8\" stroke-linecap=\"round\"/>
</svg>""",
        "results": """
<svg class=\"sp-ico\" viewBox=\"0 0 24 24\" fill=\"none\" xmlns=\"http://www.w3.org/2000/svg\">
  <path d=\"M4 19V5\" stroke=\"currentColor\" stroke-width=\"1.8\" stroke-linecap=\"round\"/>
  <path d=\"M4 19h16\" stroke=\"currentColor\" stroke-width=\"1.8\" stroke-linecap=\"round\"/>
  <path d=\"M7 17V12\" stroke=\"currentColor\" stroke-width=\"2.2\" stroke-linecap=\"round\"/>
  <path d=\"M12 17V8\" stroke=\"currentColor\" stroke-width=\"2.2\" stroke-linecap=\"round\"/>
  <path d=\"M17 17V10\" stroke=\"currentColor\" stroke-width=\"2.2\" stroke-linecap=\"round\"/>
</svg>""",
        "charts": """
<svg class=\"sp-ico\" viewBox=\"0 0 24 24\" fill=\"none\" xmlns=\"http://www.w3.org/2000/svg\">
  <path d=\"M4 19V5\" stroke=\"currentColor\" stroke-width=\"1.8\" stroke-linecap=\"round\"/>
  <path d=\"M4 19h16\" stroke=\"currentColor\" stroke-width=\"1.8\" stroke-linecap=\"round\"/>
  <path d=\"M7 15l3-3 3 2 5-6\" stroke=\"currentColor\" stroke-width=\"1.8\" stroke-linecap=\"round\" stroke-linejoin=\"round\"/>
</svg>""",
        "report": """
<svg class=\"sp-ico\" viewBox=\"0 0 24 24\" fill=\"none\" xmlns=\"http://www.w3.org/2000/svg\">
  <path d=\"M7 3h7l3 3v15H7V3z\" stroke=\"currentColor\" stroke-width=\"1.8\" stroke-linejoin=\"round\"/>
  <path d=\"M14 3v4h4\" stroke=\"currentColor\" stroke-width=\"1.8\" stroke-linejoin=\"round\"/>
  <path d=\"M9 12h6M9 16h6\" stroke=\"currentColor\" stroke-width=\"1.8\" stroke-linecap=\"round\"/>
</svg>""",
        "email": """
<svg class=\"sp-ico\" viewBox=\"0 0 24 24\" fill=\"none\" xmlns=\"http://www.w3.org/2000/svg\">
  <rect x=\"3\" y=\"5\" width=\"18\" height=\"14\" rx=\"2\" stroke=\"currentColor\" stroke-width=\"1.8\"/>
  <path d=\"M4 7l8 6 8-6\" stroke=\"currentColor\" stroke-width=\"1.8\" stroke-linecap=\"round\" stroke-linejoin=\"round\"/>
</svg>""",
        "download": """
<svg class=\"sp-ico\" viewBox=\"0 0 24 24\" fill=\"none\" xmlns=\"http://www.w3.org/2000/svg\">
  <path d=\"M12 4v10\" stroke=\"currentColor\" stroke-width=\"1.8\" stroke-linecap=\"round\"/>
  <path d=\"M8.5 10.5 12 14l3.5-3.5\" stroke=\"currentColor\" stroke-width=\"1.8\" stroke-linecap=\"round\" stroke-linejoin=\"round\"/>
  <path d=\"M4 19h16\" stroke=\"currentColor\" stroke-width=\"1.8\" stroke-linecap=\"round\"/>
</svg>""",
        "assessment": """
<svg class=\"sp-ico\" viewBox=\"0 0 24 24\" fill=\"none\" xmlns=\"http://www.w3.org/2000/svg\">
  <path d=\"M20 6 9 17l-5-5\" stroke=\"currentColor\" stroke-width=\"2.0\" stroke-linecap=\"round\" stroke-linejoin=\"round\"/>
</svg>""",
    }
    return icons.get(key, "")


# ============================
# i18n
# ============================
I18N = {
    "IT": {
        "language": "Lingua",
        "guide": "Guide",
        "setup": "Setup",
        "business_type": "Tipologia locale",
        "open_days": "Giorni apertura / mese",
        "hourly_opt": "Analisi oraria (opzionale)",

        "dayparts": "Fasce orarie",
        "dayparts_select": "Aggiungi / modifica fasce",
        "choose_opt": "Scegli un'opzione",
        "orders_day": "Ordini medi / giorno",
        "ticket": "Scontrino medio (€)",
        "start_time": "Ora inizio (HH:MM)",
        "end_time": "Ora fine (HH:MM)",

        "costs": "Costi",
        "invest": "Investimenti",
        "fte": "FTE",
        "seasonality": "Stagionalità & avviamento",

        "mode_label": "modalità",
        "pct_on_rev": "Percentuale su ricavi",
        "fixed_month": "Importo fisso mensile (€)",

        "cogs": "COGS (materie prime + packaging)",
        "labor": "Personale",
        "opex": "OPEX (utenze, servizi, manutenzioni, ecc.)",
        "mkt": "Marketing",

        "fee_title": "Fee (opzionale)",
        "fee_enable": "Attiva fee",
        "fee": "Fee",

        "occupancy": "Occupancy",
        "rent": "Affitto fisso (€ / mese)",
        "service": "Spese condominiali / oneri comuni (€ / mese)",

        "invest_enable": "Attiva investimenti",
        "capex": "CAPEX (€)",
        "deposits": "Depositi (€)",
        "immobilizations": "Immobilizzazioni (€)",
        "guarantees": "Fideiussioni / Garanzie (€)",

        "fte_enable": "Attiva stima FTE",
        "fte_method": "Metodo di calcolo",
        "fte_m1": "Da costo lavoro calcolato",
        "fte_m2": "Da incidenza target su ricavi",
        "hourly_cost": "Costo orario medio all-in (€/h)",
        "hours_per_fte": "Ore annue per 1 FTE",
        "use_y1": "Usa vista Y1",
        "target_labor": "Incidenza target Personale (%)",
        "labor_base": "Costo lavoro annuo (base)",
        "labor_hours": "Ore lavoro annue stimate",
        "fte_out": "FTE stimati",

        "season_enable": "Attiva stagionalità & ramp-up",
        "q1": "Peso Q1 (%)",
        "q2": "Peso Q2 (%)",
        "q3": "Peso Q3 (%)",
        "q4": "Peso Q4 (%)",
        "ramp_months": "Mesi di avviamento (ramp-up)",
        "ramp_floor": "Livello iniziale mese 1 (avviamento)",

        "results": "Risultati",
        "rev_run": "Ricavi annui (run-rate)",
        "ebitda_run": "EBITDA annuo (run-rate)",
        "ebitda_pct": "EBITDA % (run-rate)",
        "be_rev": "Punto di pareggio (ricavi annui)",
        "be_orders": "Punto di pareggio (ordini/giorno)",
        "cash_invested": "Capitale investito",
        "roi_run": "ROI annuo (run-rate)",
        "payback_run": "Payback (mesi, run-rate)",
        "roi_y1": "ROI annuo (Y1)",
        "payback_y1": "Payback (mesi, Y1)",
        "invest_results": "Risultati investimenti",
        "y1_results": "Vista Y1 (stagionalità & avviamento)",
        "rev_y1": "Ricavi annui (Y1)",
        "ebitda_y1": "EBITDA annuo (Y1)",
        "ebitda_pct_y1": "EBITDA % (Y1)",
        "delta_vs_run": "Delta vs run-rate",
        "fte_results": "Stima personale (FTE)",
        "labor_monthly_base": "Costo personale mensile (base)",
        "hourly_cost_used": "Costo orario usato",
        "dp_staff_split": "Ripartizione FTE per fascia",
        "dp_staff_share": "Quota ricavi",
        "dp_staff_cost": "Costo annuo allocato",
        "dp_staff_hours": "Ore annue allocate",
        "dp_staff_fte": "FTE fascia",
        "dp_staff_heads": "FTE medi in fascia",
        "na": "n/a",

        "legend": "Benchmark",
        "delta_vs": "Δ vs benchmark",
        "range": "range",

        "need_dayparts": "Seleziona almeno una fascia e inserisci ordini/scontrino per calcolare i risultati.",
        "assessment": "Valutazione",

        "charts": "Grafici",
        "rev_margin": "Fatturato & Margine (annuo)",
        "be_curve": "Curva Punto di pareggio",
        "cost_pie": "Breakdown costi (annuo)",
        "daypart_breakdown": "Breakdown per fascia (run-rate mensile)",

        "report": "Report",
        "download_xlsx": "Scarica report Excel",
        "download_pdf": "Scarica report PDF",
        "report_caption": "File pronto da condividere (KPI + grafici principali).",
        "report_email_primary": "Invio report via email",
        "report_email_caption": "Ricevi il report direttamente nella tua casella email.",
        "lead_email": "Email",
        "lead_location": "Località del progetto",
        "lead_privacy": "Ho letto la Privacy Policy e richiedo l'invio del report (obbligatorio).",
        "lead_marketing": "Acconsento a ricevere comunicazioni informative e commerciali relative ai servizi (facoltativo).",
        "privacy_policy": "Privacy Policy",
        "privacy_policy_cta": "Leggi la Privacy Policy",
        "send_pdf_email": "Invia report PDF",
        "send_xlsx_email": "Invia report Excel",
        "lead_local_downloads": "Download locale (secondario)",
        "lead_ok_title": "Richiesta registrata",
        "lead_ok_msg": "Dati validati e payload pronto per invio automatico report.",
        "lead_missing_email": "Inserisci un'email valida.",
        "lead_missing_location": "Inserisci la località del progetto.",
        "lead_missing_privacy": "Per procedere devi accettare la Privacy Policy.",
        "lead_missing_report": "Report non pronto: verifica gli input della simulazione.",
        "lead_send_error": "Operazione non completata. Riprova tra qualche istante.",
        "lead_sent_format": "Formato",
        "lead_sent_email": "Email",
        "lead_sent_location": "Località",
        "disclaimer": "Disclaimer: le valutazioni presenti hanno esclusivo scopo illustrativo e non costituiscono consulenza finanziaria, legale o base sufficiente per decisioni di investimento.",
    },
    "EN": {
        "language": "Language",
        "guide": "Guide",
        "setup": "Setup",
        "business_type": "Business type",
        "open_days": "Open days / month",
        "hourly_opt": "Hourly analysis (optional)",

        "dayparts": "Dayparts",
        "dayparts_select": "Add / edit dayparts",
        "choose_opt": "Choose an option",
        "orders_day": "Avg orders / day",
        "ticket": "Avg ticket (€)",
        "start_time": "Start time (HH:MM)",
        "end_time": "End time (HH:MM)",

        "costs": "Costs",
        "invest": "Investments",
        "fte": "FTE",
        "seasonality": "Seasonality & ramp-up",

        "mode_label": "mode",
        "pct_on_rev": "Percent of revenue",
        "fixed_month": "Fixed monthly amount (€)",

        "cogs": "COGS (food + packaging)",
        "labor": "Labor",
        "opex": "OPEX (utilities, services, maintenance, etc.)",
        "mkt": "Marketing",

        "fee_title": "Fee (optional)",
        "fee_enable": "Enable fee",
        "fee": "Fee",

        "occupancy": "Occupancy",
        "rent": "Fixed rent (€ / month)",
        "service": "Service charges (€ / month)",

        "invest_enable": "Enable investments",
        "capex": "CAPEX (€)",
        "deposits": "Deposits (€)",
        "immobilizations": "Fixed assets (€)",
        "guarantees": "Guarantees (€)",

        "fte_enable": "Enable FTE estimate",
        "fte_method": "Calculation method",
        "fte_m1": "From calculated labor cost",
        "fte_m2": "From target labor % of revenue",
        "hourly_cost": "Avg all-in hourly cost (€/h)",
        "hours_per_fte": "Annual hours per 1 FTE",
        "use_y1": "Use Y1 view",
        "target_labor": "Target labor incidence (%)",
        "labor_base": "Annual labor cost (base)",
        "labor_hours": "Estimated annual labor hours",
        "fte_out": "Estimated FTE",

        "season_enable": "Enable seasonality & ramp-up",
        "q1": "Q1 weight (%)",
        "q2": "Q2 weight (%)",
        "q3": "Q3 weight (%)",
        "q4": "Q4 weight (%)",
        "ramp_months": "Ramp-up months",
        "ramp_floor": "Month 1 starting level",

        "results": "Results",
        "rev_run": "Annual revenue (run-rate)",
        "ebitda_run": "Annual EBITDA (run-rate)",
        "ebitda_pct": "EBITDA % (run-rate)",
        "be_rev": "Break-even (annual revenue)",
        "be_orders": "Break-even (orders/day)",
        "cash_invested": "Cash invested",
        "roi_run": "Annual ROI (run-rate)",
        "payback_run": "Payback (months, run-rate)",
        "roi_y1": "Annual ROI (Y1)",
        "payback_y1": "Payback (months, Y1)",
        "invest_results": "Investment results",
        "y1_results": "Y1 view (seasonality & ramp-up)",
        "rev_y1": "Annual revenue (Y1)",
        "ebitda_y1": "Annual EBITDA (Y1)",
        "ebitda_pct_y1": "EBITDA % (Y1)",
        "delta_vs_run": "Delta vs run-rate",
        "fte_results": "Staffing estimate (FTE)",
        "labor_monthly_base": "Monthly labor cost (base)",
        "hourly_cost_used": "Hourly cost used",
        "dp_staff_split": "FTE split by daypart",
        "dp_staff_share": "Revenue share",
        "dp_staff_cost": "Allocated annual cost",
        "dp_staff_hours": "Allocated annual hours",
        "dp_staff_fte": "Daypart FTE",
        "dp_staff_heads": "Avg FTE on shift",
        "na": "n/a",

        "legend": "Benchmark",
        "delta_vs": "Δ vs benchmark",
        "range": "range",

        "need_dayparts": "Select at least one daypart and enter orders/ticket to compute results.",
        "assessment": "Assessment",

        "charts": "Charts",
        "rev_margin": "Revenue & Margin (annual)",
        "be_curve": "Break-even curve",
        "cost_pie": "Cost breakdown (annual)",
        "daypart_breakdown": "Daypart breakdown (monthly run-rate)",

        "report": "Report",
        "download_xlsx": "Download Excel report",
        "download_pdf": "Download PDF report",
        "report_caption": "Share-ready file (KPIs + key charts).",
        "report_email_primary": "Email report delivery",
        "report_email_caption": "Receive the report directly in your inbox.",
        "lead_email": "Email",
        "lead_location": "Project location",
        "lead_privacy": "I have read the Privacy Policy and request the report delivery (required).",
        "lead_marketing": "I agree to receive informational and commercial communications related to services (optional).",
        "privacy_policy": "Privacy Policy",
        "privacy_policy_cta": "Read the Privacy Policy",
        "send_pdf_email": "Send PDF report",
        "send_xlsx_email": "Send Excel report",
        "lead_local_downloads": "Local download (secondary)",
        "lead_ok_title": "Request captured",
        "lead_ok_msg": "Inputs validated and payload ready for automatic report delivery.",
        "lead_missing_email": "Please enter a valid email.",
        "lead_missing_location": "Please enter the project location.",
        "lead_missing_privacy": "To proceed, you must accept the Privacy Policy.",
        "lead_missing_report": "Report not ready: check simulation inputs.",
        "lead_send_error": "Operation could not be completed. Please try again.",
        "lead_sent_format": "Format",
        "lead_sent_email": "Email",
        "lead_sent_location": "Location",
        "disclaimer": "Disclaimer: these estimates are for illustrative purposes only and do not constitute financial or legal advice, nor a sufficient basis for investment decisions.",
    },
}



def t(key: str) -> str:
    return I18N.get(st.session_state.get("lang", "IT"), I18N["IT"]).get(key, key)


# ============================
# Inline help (tooltips)
# ============================

def h(key: str) -> str:
    """Return inline help text for inputs (language-aware)."""
    lang = st.session_state.get("lang", "IT")

    it = {
        "business_type": "Scegli un profilo benchmark (range costi) e, se previsto, fasce precompilate. Usa Custom per inserire tutto manualmente.",
        "open_days": "Numero giorni effettivi di apertura nel mese. Impatta direttamente i ricavi mensili e il break-even ordini/giorno.",
        "hourly_opt": "Se attivo, puoi inserire orari (HH:MM) per stimare ordini/ora e ricavi/ora. Non cambia il calcolo base dei ricavi.",
        "dayparts_select": "Seleziona le fasce che generano ricavi. Puoi aggiungerne più di una e modificarle in qualsiasi momento.",
        "orders_day": "Ordini medi generati da questa fascia (media giornaliera).",
        "ticket": "Scontrino medio della fascia. Se le fasce hanno ticket diversi, il modello calcola un ticket medio ponderato.",
        "start_time": "Orario inizio fascia in formato HH:MM (es. 10:00). Serve solo se Analisi oraria è attiva.",
        "end_time": "Orario fine fascia in formato HH:MM (es. 14:00).",
        "mode": "Scegli se inserire il costo come % sui ricavi (benchmark-friendly) oppure come importo fisso mensile (stress test 4-wall).",
        "cogs": "COGS = materie prime + packaging. Include ingredienti e materiali direttamente legati alla vendita (es. vaschette, carta, posate monouso). In %: incidenza su ricavi. In €: importo mensile fisso.",
        "labor": "Personale = costo del lavoro all-in (lordo + contributi + eventuali extra). In %: incidenza su ricavi. In €: importo mensile fisso (utile per scenari di staffing/turni).",
        "opex": "OPEX = costi operativi non-food (utenze, manutenzioni, servizi, cleaning, software, materiali di consumo non-food). In %: incidenza su ricavi. In €: costo mensile fisso.",
        "mkt": "Marketing = budget per comunicazione, ads, PR, promo e materiali marketing. In %: incidenza su ricavi. In €: budget mensile.",
        "fee_enable": "Attiva se esistono fee (franchising, management fee, royalty, piattaforme).",
        "fee": "Fee = royalty/management fee/commissioni piattaforme o franchising. Può essere % sui ricavi oppure importo fisso mensile. Se non applicabile, lascia disattivato.",
        "rent": "Affitto fisso mensile. È un costo rigido e impatta direttamente il break-even.",
        "service": "Oneri/spese comuni condominiali mensili (CAM). È un costo rigido e impatta direttamente il break-even.",
        "invest_enable": "Attiva per calcolare KPI investimento. ROI = EBITDA annuo / capitale investito. Payback = mesi per recuperare il capitale investito tramite EBITDA (stima semplificata).",
        "capex": "Investimento lavori + arredi + attrezzature (one-off).",
        "deposits": "Depositi (caparra, depositi cauzionali, ecc.).",
        "immobilizations": "Altre immobilizzazioni (software, licenze, avviamento, ecc.).",
        "guarantees": "Fideiussioni/garanzie (cash locked o equivalente).",
        "season_enable": "Attiva per simulare il primo anno reale: stagionalità (Q1-Q4) + avviamento (ramp-up) nei mesi iniziali.",
        "q": "Peso trimestrale sul totale annuo (somma consigliata 100%). Esempio: località estiva => Q3 più alto.",
        "ramp_months": "Numero mesi necessari a passare dal livello iniziale al 100% run-rate.",
        "ramp_floor": "Livello del mese 1 rispetto al run-rate (es. 0,65 = 65%).",
        "fte_enable": "Attiva per stimare fabbisogno personale (FTE), ore annue e costo del lavoro per fascia.",
        "fte_method": "m1: usa il costo personale già calcolato dal modello. m2: usa una % target sui ricavi per stimare il costo personale e quindi gli FTE.",
        "hourly_cost": "Costo orario medio all-in (lordo + contributi + extra). Puoi usare slider o inserire un valore libero.",
        "hours_per_fte": "Ore annue equivalenti per 1 FTE (es. 1.720–1.900). Puoi usare preset, slider o valore libero.",
        "use_y1": "Se attivo, usa la vista Y1 (stagionalità/ramp-up) per stimare FTE sul primo anno invece del run-rate.",
        "target_labor": "Se usi m2: incidenza target del personale sui ricavi (stima del costo lavoro). Puoi inserire un valore libero.",
    }

    en = {
        "business_type": "Pick a benchmark profile (cost ranges) and optional default dayparts. Use Custom for full manual input.",
        "open_days": "Actual open days per month. Directly impacts monthly revenue and break-even orders/day.",
        "hourly_opt": "If enabled, you can enter daypart times (HH:MM) to estimate orders/hour and revenue/hour. Base revenue calc is unchanged.",
        "dayparts_select": "Select the revenue-generating dayparts. You can add multiple and edit anytime.",
        "orders_day": "Average daily orders generated by this daypart.",
        "ticket": "Average ticket for the daypart. The model computes a weighted blended ticket across dayparts.",
        "start_time": "Daypart start time in HH:MM (e.g., 10:00). Only used if Hourly analysis is enabled.",
        "end_time": "Daypart end time in HH:MM (e.g., 14:00).",
        "mode": "Choose % of revenue (benchmark-friendly) or fixed monthly € (4-wall stress test).",
        "cogs": "COGS = food + packaging. Includes ingredients and direct disposables tied to sales (e.g., trays, paper, cutlery). In %: incidence on revenue. In €: fixed monthly amount.",
        "labor": "Labor = all-in labor cost (gross wages + contributions + extras). In %: incidence on revenue. In €: fixed monthly amount (useful for staffing/shift scenarios).",
        "opex": "OPEX = non-food operating expenses (utilities, maintenance, services, cleaning, software, non-food consumables). In %: incidence on revenue. In €: fixed monthly amount.",
        "mkt": "Marketing = budget for communication, ads, PR, promos and marketing materials. In %: incidence on revenue. In €: monthly budget.",
        "fee_enable": "Enable only if fees apply (royalty, management fee, platforms).",
        "fee": "Fee = royalty/management fee/platform commissions or franchising fee. Can be % of revenue or fixed monthly €. Disable if not applicable.",
        "rent": "Fixed monthly rent. Rigid cost that directly impacts break-even.",
        "service": "Monthly service charges / common area fees (CAM). Rigid cost that directly impacts break-even.",
        "invest_enable": "Enable investment KPIs. ROI = annual EBITDA / cash invested. Payback = months needed to recover invested cash through EBITDA (simplified estimate).",
        "capex": "Fit-out + furniture + equipment (one-off).",
        "deposits": "Deposits (security deposits, key money, etc.).",
        "immobilizations": "Other fixed assets (software, licenses, start-up costs, etc.).",
        "guarantees": "Guarantees / bonds (cash locked or equivalent).",
        "season_enable": "Enable to simulate real Year 1: seasonality (Q1-Q4) + initial ramp-up.",
        "q": "Quarter weights (recommended total 100%). Example: summer location => higher Q3.",
        "ramp_months": "Months needed to ramp from starting level to 100% run-rate.",
        "ramp_floor": "Month 1 level vs run-rate (e.g., 0.65 = 65%).",
        "fte_enable": "Enable staffing estimation (FTE), annual hours and labor cost by daypart.",
        "fte_method": "m1: uses labor cost already computed by the model. m2: uses a target labor % on revenue to estimate labor cost and then FTE.",
        "hourly_cost": "Average all-in hourly cost (gross + contributions + extras). You can use slider or free value.",
        "hours_per_fte": "Annual hours for 1 FTE equivalent (e.g., 1,720–1,900). You can use presets, slider or free value.",
        "use_y1": "If enabled, uses Y1 view (seasonality/ramp-up) to estimate FTE on Year 1 instead of run-rate.",
        "target_labor": "If using m2: target labor % of revenue (labor cost estimate). Free value supported.",
    }

    m = it if lang == "IT" else en
    return m.get(key, "")


def _help_inline_html(text: str) -> str:
    lang_it = st.session_state.get("lang", "IT") == "IT"
    txt = _html_escape(str(text or "")).replace("\n", "<br>")
    title = _html_escape("Mostra spiegazione" if lang_it else "Show explanation")
    return (
        f'<details class="sp-help-inline"><summary title="{title}">?</summary>'
        f'<div class="sp-help-panel">{txt}</div></details>'
    )


def minihead_with_help(icon_key: str, title: str, help_text: str) -> None:
    st.markdown(
        f'<div class="sp-minihead">{icon(icon_key)} <span>{_html_escape(title)}</span>{_help_inline_html(help_text)}</div>',
        unsafe_allow_html=True,
    )


def premium_info(text: str) -> None:
    st.markdown(
        f'<div class="sp-info-card">{_html_escape(text)}</div>',
        unsafe_allow_html=True,
    )


def premium_notice(text: str, level: str = "neutral") -> None:
    level = str(level or "neutral").lower()
    cls = "sp-notice-neutral"
    if level == "ok":
        cls = "sp-notice-ok"
    elif level == "warn":
        cls = "sp-notice-warn"
    st.markdown(
        f'<div class="sp-info-card {cls}">{_html_escape(text)}</div>',
        unsafe_allow_html=True,
    )


def _secret_get(key: str, default: Any = None) -> Any:
    """Safe secrets accessor: returns default if secrets are not configured."""
    try:
        return st.secrets.get(key, default)
    except Exception:
        return default


def _render_privacy_policy_view() -> None:
    lang = st.session_state.get("lang", "IT")

    st.title("Privacy Policy - StorePilot")
    st.caption("Ultimo aggiornamento: 23 febbraio 2026" if lang == "IT" else "Last update: February 23, 2026")

    if lang == "IT":
        st.markdown(
            f"""
**1. Titolare del trattamento**  
Il titolare del trattamento dei dati personali raccolti attraverso questo strumento e StorePilot, progetto digitale operante nel settore food retail.  
Per qualsiasi richiesta inerente alla privacy e possibile scrivere all'indirizzo: privacy@storepilot.eu

**2. Tipologie di dati trattati**  
I dati personali trattati attraverso StorePilot comprendono:
- Email fornita dall'utente
- Localita del progetto inserita dall'utente
- Parametri di simulazione inseriti dall'utente nel tool
- KPI generati dal simulatore (es. ricavi, EBITDA, break-even)
- Timestamp di invio richiesta
- Origine lead (es. "StorePilot - Horeca Consulting" o altra fonte)
- Versione del tool utilizzata
- Flag di consenso (privacy e, se esplicitato, marketing)

**3. Finalita del trattamento**  
I dati personali sono trattati per le seguenti finalita:  
a) Invio del report richiesto dall'utente tramite il simulatore;  
b) Invio di comunicazioni informative e commerciali relative ai servizi StorePilot e ad attivita di consulenza correlate, soltanto se l'utente ha prestato consenso esplicito a tale finalita.

**4. Base giuridica del trattamento**  
- Per l'invio del report richiesto: esecuzione di una richiesta dell'utente, ai sensi dell'art. 6(1)(b) del GDPR.  
- Per l'invio di comunicazioni commerciali: consenso espresso dall'interessato, ai sensi dell'art. 6(1)(a) del GDPR.

**5. Modalita di trattamento e sicurezza**  
I dati sono trattati con strumenti digitali e automatizzati, adottando misure tecniche e organizzative adeguate per garantire sicurezza, riservatezza e integrita, incluso:
- accesso limitato e controllato ai dati;
- uso di credenziali e chiavi sicure lato server;
- protezione delle comunicazioni;
- segregazione dei dati in sistemi e servizi protetti.
I dati non sono sottoposti a processi di decisione automatizzata o profilazione.

**6. Periodo di conservazione**  
- Lead senza consenso marketing: conservati per un massimo di 12 mesi dalla data di raccolta.  
- Lead con consenso marketing: conservati per un massimo di 24 mesi o fino a revoca del consenso.

**7. Destinatari dei dati / responsabili esterni**  
I dati possono essere comunicati a soggetti o servizi terzi per le finalita indicate, in qualita di responsabili del trattamento:
- Streamlit Cloud (hosting e operativita applicativa)
- MailerSend (servizio di invio email)
- Google Sheets o servizi analoghi (ove attivati per salvataggio dati)
- Fornitori di servizi tecnici necessari all'erogazione dell'applicazione

**8. Trasferimenti di dati verso paesi terzi**  
I dati possono essere trasferiti verso paesi al di fuori dello Spazio Economico Europeo (ad esempio nel caso di utilizzo di servizi cloud internazionali). Tali trasferimenti avvengono nel rispetto delle norme GDPR e con adeguate garanzie (come Standard Contractual Clauses ove applicabili).

**9. Diritti dell'interessato**  
Gli interessati possono in qualsiasi momento esercitare i diritti garantiti dagli articoli 15-22 del GDPR, tra cui:
- accesso ai propri dati personali;
- rettifica o aggiornamento dei dati;
- cancellazione ("diritto all'oblio");
- limitazione del trattamento;
- opposizione al trattamento;
- portabilita dei dati.
Le richieste possono essere inviate a privacy@storepilot.eu. L'interessato ha inoltre il diritto di proporre reclamo a un'autorita di controllo.

**10. Minori**  
Il servizio non e pensato ne rivolto a persone di eta inferiore ai 16 anni. Qualora un minore fornisse dati personali, si invita un genitore/tutore a contattare il titolare per richiederne la cancellazione.

**11. Cookie e strumenti di tracciamento**  
Il tool puo utilizzare cookie tecnici e funzionali necessari al corretto funzionamento della piattaforma (in particolare per Streamlit Cloud). Non vengono utilizzati strumenti di tracciamento pubblicitario o profilazione per finalita di marketing, ne pixel di terze parti per advertising.

**12. Modifiche alla privacy policy**  
La presente informativa puo essere soggetta ad aggiornamenti. La data dell'ultimo aggiornamento e indicata in testa alla pagina.
"""
        )
        st.markdown("[← Torna a StorePilot](/)")
    else:
        st.markdown(
            f"""
**Controller**  
StorePilot.

**Data processed**  
Email, project location, simulation inputs, generated KPIs, timestamp, lead source, tool version.

**Purposes & legal basis**  
Report delivery (GDPR Art. 6(1)(b)); optional follow-up contact with consent (GDPR Art. 6(1)(a)).

**Retention**  
12 months (non-converted leads), 24 months with marketing consent or until withdrawal.

**Processors (if enabled)**  
Streamlit Cloud, MailerSend, Google Sheets/Drive.

**International transfers**  
May occur with appropriate GDPR safeguards (including SCC where applicable).

**Rights**  
Access, rectification, erasure, restriction, objection, portability, and complaint to supervisory authority.

**Privacy contact**  
privacy@storepilot.eu

**Minors and cookies**  
Not intended for users under 16. Technical cookies may be used for Streamlit operations. No marketing tracking in this feature unless specifically disclosed.
"""
        )
        st.markdown("[← Back to StorePilot](/)")


def _is_privacy_view() -> bool:
    try:
        return str(st.query_params.get("view", "") or "").strip().lower() == "privacy"
    except Exception:
        return False


if _is_privacy_view():
    _render_privacy_policy_view()
    st.stop()


def _is_valid_email(email: str) -> bool:
    s = str(email or "").strip()
    if not s:
        return False
    return bool(re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", s))


def build_lead_payload(
    *,
    email: str,
    project_location: str,
    privacy_consent: bool,
    marketing_consent: bool,
    report_format: str,
    results: Dict[str, Any],
    lang: str,
) -> Dict[str, Any]:
    return {
        "email": str(email).strip().lower(),
        "project_location": str(project_location).strip(),
        "privacy_consent": bool(privacy_consent),
        "marketing_consent": bool(marketing_consent),
        "report_format": str(report_format).lower(),
        "timestamp_utc": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "tool_version": TOOL_VERSION,
        "lead_source": LEAD_SOURCE,
        "language": str(lang).upper(),
        "kpi_revenue_annual_runrate": float(results.get("revenue_annual_runrate", 0.0) or 0.0),
        "kpi_ebitda_annual_runrate": float(results.get("ebitda_annual_runrate", 0.0) or 0.0),
        "kpi_ebitda_pct_annual_runrate": float(results.get("ebitda_pct_annual_runrate", 0.0) or 0.0),
        "kpi_break_even_revenue_annual": float(results.get("break_even_revenue_annual", 0.0) or 0.0),
        "kpi_break_even_orders_day": float(results.get("break_even_orders_day", 0.0) or 0.0),
        "kpi_cash_invested": float(results.get("cash_invested", 0.0) or 0.0),
        "kpi_roi_annual": float(results.get("roi_annual", 0.0) or 0.0),
        "kpi_payback_months": float(results.get("payback_months", 0.0) or 0.0),
    }


def save_lead_to_google_sheet(payload: Dict[str, Any]):
    try:
        creds = Credentials.from_service_account_info(
            st.secrets["gcp_service_account"],
            scopes=[
                "https://www.googleapis.com/auth/spreadsheets",
                "https://www.googleapis.com/auth/drive",
            ],
        )
        client = gspread.authorize(creds)

        spreadsheet = client.open("StorePilot_Leads")
        sheet = spreadsheet.worksheet("Leads")

        header = [str(c).strip() for c in (sheet.row_values(1) or []) if str(c).strip()]
        if not header:
            st.error("Errore salvataggio lead: header mancante nel tab 'Leads'.")
            return False

        def _pick(*keys: str, default: Any = "") -> Any:
            for k in keys:
                if k in payload and payload.get(k) is not None:
                    return payload.get(k)
            return default

        now_utc = datetime.utcnow().isoformat()
        privacy_consent = bool(_pick("privacy_consent", default=False))
        marketing_consent = bool(_pick("marketing_consent", default=False))
        row_data: Dict[str, Any] = {
            "lead_id": str(uuid4()),
            "email": str(_pick("email", default="")).strip(),
            "localita": str(_pick("localita", "location", "project_location", default="")).strip(),
            "consenso": "true" if privacy_consent else "false",
            "consenso_privacy": "true" if privacy_consent else "false",
            "consenso_marketing": "true" if marketing_consent else "false",
            "rev_year": _pick("rev_year", "consenso_rev_year", "kpi_revenue_annual_runrate", default=""),
            "ebitda_year": _pick("ebitda_year", "kpi_ebitda_annual_runrate", default=""),
            "ebitda_pct": _pick("ebitda_pct", "kpi_ebitda_pct_annual_runrate", default=""),
            "break_even_rev_year": _pick("break_even_rev_year", "kpi_break_even_revenue_annual", default=""),
            "tool_version": _pick("tool_version", default=TOOL_VERSION),
            "source": "StorePilot – Horeca Consulting",
            "timestamp": now_utc,
            "cta_clicked": _pick("cta_clicked", "report_format", default=""),
            "cta_clicked_at": _pick("cta_clicked_at", default=now_utc),
            "booked_call": _pick("booked_call", default=""),
        }

        row_values = [row_data.get(col, "") for col in header]
        sheet.append_row(row_values, value_input_option="USER_ENTERED")

        return True
    except Exception as e:
        st.error(f"Errore salvataggio lead: {e}")
        return False


def save_to_sheet(payload: Dict[str, Any]) -> Tuple[bool, str]:
    try:
        email = str(payload.get("email", "") or "").strip()
        if not email:
            return False, "missing_email"

        success = save_lead_to_google_sheet(payload)
        return (True, "saved") if success else (False, "sheet_error")
    except Exception:
        return False, "sheet_error"


def send_email_report(
    *,
    to_email: str,
    report_bytes: bytes,
    report_filename: str,
    report_mime: str,
    payload: Dict[str, Any],
) -> Tuple[bool, str]:
    try:
        # Placeholder for MailerSend integration.
        # Internal copy email is intentionally read only from secrets.
        _ = to_email
        _ = report_bytes
        _ = report_filename
        _ = report_mime
        _ = payload
        _ = _secret_get("mailersend_api_key", "")
        _ = _secret_get("internal_report_copy_email", "")
        return True, "ready"
    except Exception:
        return False, "email_error"


def slider_with_free_input(
    *,
    label: str,
    key: str,
    min_value: float,
    max_value: float,
    step: float,
    default_value: float,
    help_text: str = "",
) -> float:
    st.session_state.setdefault(key, float(default_value))
    current = float(st.session_state.get(key, default_value) or default_value)
    current_slider = max(min_value, min(max_value, current))

    lang_it = st.session_state.get("lang", "IT") == "IT"
    range_lbl = "Range consigliato" if lang_it else "Suggested range"
    free_lbl = "Valore libero" if lang_it else "Free value"

    c1, c2 = st.columns([1.7, 1.1])
    with c1:
        st.caption(range_lbl)
        slider_val = st.slider(
            label,
            min_value=float(min_value),
            max_value=float(max_value),
            value=float(current_slider),
            step=float(step),
            key=f"{key}__slider",
            help=help_text,
            label_visibility="collapsed",
        )
    with c2:
        free_val = st.number_input(
            free_lbl,
            min_value=0.0,
            value=float(current),
            step=float(step),
            key=f"{key}__free",
            help=help_text,
        )

    final_val = current
    if abs(float(free_val) - current) > 1e-9:
        final_val = float(free_val)
    elif abs(float(slider_val) - current_slider) > 1e-9:
        final_val = float(slider_val)

    st.session_state[key] = float(final_val)
    return float(final_val)


# ============================
# Dayparts defaults logic
# ============================

DAYPARTS_WIDGET_KEY = "selected_dayparts_widget"

def _apply_profile_dayparts(force: bool = False) -> None:
    try:
        bt_key = st.session_state.get("business_type_key")
        bt_obj = bt_by_key.get(bt_key, {})
        defaults = list(bt_obj.get("default_dayparts", []) or [])
    except Exception:
        defaults = []

    # NOTE: do not write into the multiselect widget key after it has been instantiated.
    # We only pre-seed the separate widget key BEFORE rendering, or via rerun flags.
    if force or (not st.session_state.get("dayparts_customized", False)):
        st.session_state["_applying_profile_defaults"] = True
        st.session_state["selected_dayparts"] = defaults
        # Pre-seed the widget value for the next render
        st.session_state[DAYPARTS_WIDGET_KEY] = defaults
        st.session_state["dayparts_customized"] = False
        st.session_state["_applying_profile_defaults"] = False


def _on_business_type_change() -> None:
    """When the business type changes, auto-load its default dayparts unless the user already customized."""
    _apply_profile_dayparts(force=False)


def _on_dayparts_widget_change() -> None:
    """Sync widget selection into the canonical state and mark as customized.

    Canonical: selected_dayparts
    Widget:    selected_dayparts_widget (avoids Streamlit restriction on mutating widget state).
    """
    if st.session_state.get("_applying_profile_defaults", False):
        return

    sel = list(st.session_state.get(DAYPARTS_WIDGET_KEY, []) or [])
    st.session_state["selected_dayparts"] = sel
    st.session_state["dayparts_customized"] = True


def _css():
    st.markdown(
        """
<style>
  :root{
    --sp-bg: #f3eee9;
    --sp-card: #ffffff;
    --sp-card2: #fbf8f5;
    --sp-border: rgba(16,24,40,0.10);
    --sp-text: #1b1b1b;
    --sp-muted: #5a504a;
    --sp-muted2:#6b5f58;
    --sp-shadow: 0 18px 45px rgba(16,24,40,0.10);
    --sp-shadow2: 0 12px 26px rgba(16,24,40,0.08);
    --sp-radius: 22px;
    --sp-radius2: 16px;
    --sp-accent: #84665B;     /* Horeca brown */
    --sp-accent2: #B89581;    /* Horeca light brown */
    --sp-neg: #A64A4A;        /* Negative (brand-consistent red) */
    --sp-neg-soft: rgba(166,74,74,0.08);
    --sp-input-bg: #f8f4ef;          /* warm beige input background */
    --sp-input-bg-hover: #f3eee9;    /* slightly deeper on hover */
    --sp-dark: #1c1c1c;            /* hero / logo box tone */
  }

  .stApp { background: var(--sp-bg); }
  .block-container { padding-top: 18px; padding-bottom: 42px; max-width: 1320px; }
  header[data-testid="stHeader"] { background: transparent; }
  div[data-testid="stVerticalBlock"] > div { gap: 0.85rem; }
  /* Inline SVG icons */
  .sp-ico{
    width: 18px;
    height: 18px;
    margin-right: 10px;
    color: var(--sp-accent2);
    flex: 0 0 auto;
    vertical-align: -3px;
  }
/* Expanders: remove the big white pill headers */
details[data-testid="stExpander"]{
  border: 0 !important;
  background: transparent !important;
  box-shadow: none !important;
}

details[data-testid="stExpander"] > summary{
  background: transparent !important;
  border: 0 !important;
  box-shadow: none !important;
  padding: 0 !important;
  margin: 0 !important;
}

details[data-testid="stExpander"] > summary:hover{
  background: transparent !important;
}

/* reduce the header inner spacing to avoid a “bar” */
details[data-testid="stExpander"] > summary > div{
  padding: 0 !important;
  margin: 0 !important;
}

details[data-testid="stExpander"] div[data-testid="stExpanderDetails"]{
  border: 0 !important;
  background: transparent !important;
  box-shadow: none !important;
  padding-top: 10px !important;
}
  
  /* Cards */
  .sp-card{
    background: rgba(255,255,255,0.96);
    border: 1px solid rgba(16,24,40,0.10);
    border-radius: var(--sp-radius);
    padding: 16px 18px;
    box-shadow: 0 14px 34px rgba(16,24,40,0.10);
    transition: transform 160ms ease, box-shadow 160ms ease, border-color 160ms ease;
  }
  .sp-card:hover{
    transform: translateY(-1px);
    box-shadow: 0 18px 46px rgba(16,24,40,0.13);
    border-color: rgba(16,24,40,0.14);
  }
  .sp-card.soft{
    background: linear-gradient(180deg, rgba(255,255,255,0.98) 0%, rgba(251,248,245,0.98) 100%);
  }

  /* Hero */
  .sp-hero{
    background: linear-gradient(180deg, #1c1c1c 0%, #2a2a2a 100%);
    border: 1px solid rgba(255,255,255,0.06);
    border-radius: var(--sp-radius);
    box-shadow: 0 22px 60px rgba(0,0,0,0.35);
    margin: 0 auto 22px auto;
    max-width: 1040px;
    padding: 22px 32px;
    min-height: 170px;
    display: flex;
    flex-direction: column;
    justify-content: center;
    gap: 14px;
  }

  .sp-hero-inner{
    display: flex;
    align-items: center;
    gap: 26px;
  }

  .sp-hero-logo{
    height: 150px;
    width: auto;
    display: block;
  }

  .sp-hero-copy{
    display: flex;
    flex-direction: column;
    gap: 6px;
  }

  .sp-hero .sub{
    font-size: 22px;
    color: #ffffff;
    font-weight: 950;
    margin: 0;
    letter-spacing: -0.2px;
    line-height: 1.15;
  }

  .sp-hero .payoff{
    font-size: 13px;
    color: #cfcfcf;
    margin: 0;
    font-weight: 800;
    letter-spacing: 1.1px;
    text-transform: uppercase;
  }

  .sp-hero-desc{
    display: flex;
    gap: 14px;
    align-items: flex-start;
    padding-top: 14px;
    border-top: 1px solid rgba(255,255,255,0.08);
  }
  .sp-hero-desc .accent{
    border-left: 4px solid var(--sp-accent2);
    padding-left: 14px;
    box-shadow: -1px 0 0 rgba(184,149,129,0.55), -6px 0 18px rgba(184,149,129,0.18);
  }
  .sp-hero-desc p{
    font-size: 15px;
    color: #eaeaea;
    margin: 0;
    line-height: 1.55;
    font-weight: 520;
  }
  .sp-hero-desc strong{
    color: #ffffff;
    font-weight: 800;
  }

  @media (max-width: 860px){
    .sp-hero-inner{ flex-direction: column; align-items: flex-start; gap: 14px; }
    .sp-hero-logo{ height: 110px; }
    .sp-hero .sub{ font-size: 20px; }
    .sp-hero{ padding: 20px 22px; }
    .sp-hero-desc{ padding-top: 12px; }
  }

  /* Titles */
  .sp-title{
    font-size: 28px;
    font-weight: 900;
    color: var(--sp-text);
    letter-spacing: -0.3px;
    margin: 4px 0 12px 0;
    display: inline-block;
    border-bottom: 3px solid rgba(184,149,129,0.35);
    padding-bottom: 6px;
  }
  .sp-subtitle{
    font-size: 14px;
    color: var(--sp-muted2);
    margin-top: -6px;
    margin-bottom: 12px;
  }
  /* Mini headers before expanders (SVG + title) */
  .sp-minihead{
    display:flex;
    align-items:center;
    gap:10px;
    font-weight: 950;
    color: var(--sp-text);
    margin: 10px 0 6px 2px;
  }
  .sp-minihead .sp-help-inline{
    margin-left: 6px;
  }
  .sp-minihead .sp-help-inline > summary{
    width: 24px;
    height: 24px;
    font-size: 12px;
    box-shadow: 0 6px 14px rgba(16,24,40,0.12);
  }
  .sp-minihead .sp-help-panel{
    top: 30px;
    width: min(420px, 72vw);
  }
  .sp-info-card{
    margin: 8px 0 10px 0;
    border: 1px solid rgba(132,102,91,0.24);
    border-radius: 14px;
    background: linear-gradient(180deg, rgba(255,255,255,0.98), rgba(247,241,236,0.98));
    box-shadow: 0 10px 22px rgba(16,24,40,0.08);
    padding: 10px 12px;
    color: var(--sp-text);
    font-size: 13px;
    line-height: 1.45;
    position: relative;
  }
  .sp-info-card::before{
    content: "i";
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 18px;
    height: 18px;
    border-radius: 999px;
    margin-right: 8px;
    background: rgba(132,102,91,0.90);
    color: #fff;
    font-weight: 900;
    font-size: 11px;
  }
  .sp-info-card.sp-notice-warn{
    border-color: rgba(166,74,74,0.26);
    background: linear-gradient(180deg, rgba(255,251,250,0.98), rgba(251,242,239,0.98));
  }
  .sp-info-card.sp-notice-warn::before{
    content: "!";
    background: rgba(166,74,74,0.92);
  }
  .sp-info-card.sp-notice-ok{
    border-color: rgba(38,171,95,0.30);
    background: linear-gradient(180deg, rgba(249,255,251,0.98), rgba(239,250,244,0.98));
  }
  .sp-info-card.sp-notice-ok::before{
    content: "✓";
    background: rgba(38,171,95,0.92);
  }
  .sp-info-card.sp-notice-neutral{
    border-color: rgba(132,102,91,0.24);
  }
  .sp-guide-hero{
    margin: 4px 0 12px 0;
    border: 1px solid rgba(16,24,40,0.12);
    border-radius: 16px;
    padding: 12px 14px;
    background: linear-gradient(180deg, rgba(255,255,255,0.98) 0%, rgba(249,245,241,0.98) 100%);
    box-shadow: 0 10px 22px rgba(16,24,40,0.07);
  }
  .sp-guide-hero .k{
    font-size: 12px;
    color: var(--sp-muted2);
    text-transform: uppercase;
    letter-spacing: 0.7px;
    font-weight: 900;
    margin: 0 0 4px 0;
  }
  .sp-guide-hero .v{
    margin: 0;
    font-size: 15px;
    font-weight: 780;
    line-height: 1.45;
    color: var(--sp-text);
  }
  .sp-guide-grid{
    display: grid;
    grid-template-columns: repeat(3, minmax(0, 1fr));
    gap: 10px;
    margin: 8px 0 10px 0;
  }
  .sp-guide-card{
    border: 1px solid rgba(16,24,40,0.12);
    border-radius: 14px;
    padding: 10px 12px;
    background: rgba(255,255,255,0.94);
    box-shadow: 0 8px 18px rgba(16,24,40,0.06);
  }
  .sp-guide-card .h{
    margin: 0 0 6px 0;
    font-size: 13px;
    font-weight: 900;
    color: var(--sp-text);
  }
  .sp-guide-card .b{
    margin: 0;
    font-size: 12px;
    color: var(--sp-muted2);
    line-height: 1.42;
  }
  .sp-guide-step{
    display: flex;
    align-items: flex-start;
    gap: 10px;
    margin: 8px 0;
    padding: 9px 10px;
    border: 1px solid rgba(16,24,40,0.10);
    border-radius: 12px;
    background: rgba(255,255,255,0.9);
  }
  .sp-guide-step .n{
    width: 22px;
    height: 22px;
    border-radius: 999px;
    flex: 0 0 22px;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    font-size: 11px;
    font-weight: 900;
    color: #fff;
    background: rgba(132,102,91,0.92);
    box-shadow: 0 6px 14px rgba(16,24,40,0.12);
  }
  .sp-guide-step .t{
    margin: 0;
    font-size: 13px;
    color: var(--sp-text);
    line-height: 1.4;
  }
  .sp-guide-tip{
    margin-top: 10px;
    border: 1px dashed rgba(132,102,91,0.35);
    border-radius: 12px;
    background: rgba(255,255,255,0.85);
    padding: 10px 12px;
    color: var(--sp-muted);
    font-size: 12px;
    line-height: 1.4;
  }
  .sp-glossary-hero{
    margin: 4px 0 12px 0;
    border: 1px solid rgba(16,24,40,0.12);
    border-radius: 16px;
    padding: 12px 14px;
    background: linear-gradient(180deg, rgba(255,255,255,0.98) 0%, rgba(249,245,241,0.98) 100%);
    box-shadow: 0 10px 22px rgba(16,24,40,0.07);
  }
  .sp-glossary-hero .k{
    font-size: 12px;
    color: var(--sp-muted2);
    text-transform: uppercase;
    letter-spacing: 0.7px;
    font-weight: 900;
    margin: 0 0 4px 0;
  }
  .sp-glossary-hero .v{
    margin: 0;
    font-size: 15px;
    font-weight: 760;
    line-height: 1.45;
    color: var(--sp-text);
  }
  .sp-glossary-grid{
    display: grid;
    grid-template-columns: repeat(3, minmax(0, 1fr));
    gap: 10px;
    margin: 8px 0 10px 0;
  }
  .sp-glossary-card{
    border: 1px solid rgba(16,24,40,0.12);
    border-radius: 14px;
    padding: 10px 12px;
    background: rgba(255,255,255,0.94);
    box-shadow: 0 8px 18px rgba(16,24,40,0.06);
  }
  .sp-glossary-card .h{
    margin: 0 0 6px 0;
    font-size: 13px;
    font-weight: 900;
    color: var(--sp-text);
  }
  .sp-glossary-card ul{
    margin: 0;
    padding-left: 16px;
  }
  .sp-glossary-card li{
    margin: 4px 0;
    font-size: 12px;
    color: var(--sp-muted2);
    line-height: 1.4;
  }
  .sp-glossary-note{
    margin-top: 10px;
    border: 1px dashed rgba(132,102,91,0.35);
    border-radius: 12px;
    background: rgba(255,255,255,0.85);
    padding: 10px 12px;
    color: var(--sp-muted);
    font-size: 12px;
    line-height: 1.4;
  }
  @media (max-width: 860px){
    .sp-guide-grid{ grid-template-columns: 1fr; }
    .sp-glossary-grid{ grid-template-columns: 1fr; }
  }
  .sp-scorebar{
    margin-top: 8px;
    height: 10px;
    border-radius: 999px;
    background: rgba(16,24,40,0.10);
    overflow: hidden;
    border: 1px solid rgba(16,24,40,0.10);
    box-shadow: inset 0 1px 2px rgba(16,24,40,0.08);
  }
  .sp-scorebar > span{
    display: block;
    height: 100%;
    border-radius: 999px;
    background: linear-gradient(90deg, rgba(184,149,129,0.95), rgba(132,102,91,0.98));
    box-shadow: 0 4px 10px rgba(132,102,91,0.28);
  }
  /* Emphasized mini-header (used for Assessment) */
  .sp-minihead.emph{
    font-size: 16px;
    font-weight: 980;
    padding: 10px 12px;
    border-radius: 14px;
    background: rgba(255,255,255,0.55);
    border: 1px solid rgba(16,24,40,0.10);
    box-shadow: 0 10px 22px rgba(16,24,40,0.06);
  }
  /* Decision badge (GO / REVIEW / NO GO) */
  .sp-badge{
    margin-left: auto;
    display: inline-flex;
    align-items: center;
    gap: 8px;
    padding: 6px 10px;
    border-radius: 999px;
    font-size: 12px;
    font-weight: 950;
    letter-spacing: 0.5px;
    text-transform: uppercase;
    border: 1px solid rgba(16,24,40,0.10);
    background: rgba(255,255,255,0.72);
    box-shadow: 0 10px 22px rgba(16,24,40,0.06);
  }
  .sp-dot{
    width: 9px;
    height: 9px;
    border-radius: 999px;
    background: rgba(90,80,74,0.55);
    box-shadow: 0 0 0 3px rgba(184,149,129,0.18);
  }
  .sp-badge.go{
    border-color: rgba(16,24,40,0.10);
    box-shadow: 0 10px 22px rgba(16,24,40,0.06), 0 0 0 3px rgba(38,171,95,0.10);
  }
  .sp-badge.go .sp-dot{ background: rgba(38,171,95,0.95); box-shadow: 0 0 0 3px rgba(38,171,95,0.14); }

  .sp-badge.review{
    border-color: rgba(16,24,40,0.10);
    box-shadow: 0 10px 22px rgba(16,24,40,0.06), 0 0 0 3px rgba(244,177,21,0.10);
  }
  .sp-badge.review .sp-dot{ background: rgba(244,177,21,0.95); box-shadow: 0 0 0 3px rgba(244,177,21,0.14); }

  .sp-badge.nogo{
    border-color: rgba(16,24,40,0.10);
    box-shadow: 0 10px 22px rgba(16,24,40,0.06), 0 0 0 3px rgba(225,74,74,0.10);
  }
  .sp-badge.nogo .sp-dot{ background: rgba(225,74,74,0.95); box-shadow: 0 0 0 3px rgba(225,74,74,0.14); }

  /* Hide Streamlit default dividers/HRs (they create unwanted bars) */
  div[data-testid="stDivider"],
  div[data-testid="stDivider"] > div,
  div[data-testid="stDivider"] hr,
  .stApp hr{
    display: none !important;
    height: 0 !important;
    border: 0 !important;
    margin: 0 !important;
    padding: 0 !important;
    background: transparent !important;
    box-shadow: none !important;
  }
  /* Hide any remaining HRs generated inside markdown containers */
  div[data-testid="stMarkdownContainer"] hr{
    display: none !important;
  }

  /* Section divider: dark (hero tone), subtle gradient */
  .sp-section-divider{
    height: 1px;
    width: 100%;
    margin: 18px 0 16px 0;
    background: linear-gradient(
      90deg,
      rgba(28,28,28,0.00),
      rgba(28,28,28,0.42),
      rgba(28,28,28,0.00)
    );
  }
  /* Internal divider used between sub-blocks: keep it as a thin dark line (not a white box) */
  .sp-divider{
    height: 1px;
    width: 100%;
    margin: 14px 0 12px 0;
    border-radius: 0 !important;
    background: linear-gradient(
      90deg,
      rgba(28,28,28,0.00),
      rgba(28,28,28,0.28),
      rgba(28,28,28,0.00)
    ) !important;
    box-shadow: none !important;
  }
    /* Cost section title + inline help dot */
  .sp-cost-title{
    display:flex;
    align-items:center;
    gap:10px;
    font-weight: 900;
    color: var(--sp-text);
    margin: 0 0 6px 0;
  }
  .sp-help-dot{
    width: 18px;
    height: 18px;
    border-radius: 999px;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    font-size: 12px;
    font-weight: 900;
    color: rgba(255,255,255,0.92);
    background: rgba(132,102,91,0.85);
    border: 1px solid rgba(255,255,255,0.10);
    box-shadow: 0 8px 16px rgba(16,24,40,0.10);
    cursor: help;
    user-select: none;
  }

  /* KPI cards */
  .sp-metric{
    background: linear-gradient(180deg, rgba(255,255,255,0.98) 0%, rgba(250,247,244,0.98) 100%);
    border: 1px solid rgba(16,24,40,0.12);
    border-radius: 18px;
    padding: 14px 16px;
    box-shadow: 0 12px 26px rgba(16,24,40,0.08);
    height: 100%;
    transition: transform 160ms ease, box-shadow 160ms ease, border-color 160ms ease;
  }
  .sp-metric:hover{
    transform: translateY(-1px);
    box-shadow: 0 16px 34px rgba(16,24,40,0.11);
    border-color: rgba(16,24,40,0.16);
  }
  .sp-metric .k{
    font-size: 12px;
    color: var(--sp-muted2);
    font-weight: 850;
    margin: 0 0 6px 0;
  }
  .sp-metric .v{
    font-size: 30px;
    font-weight: 950;
    margin: 0;
    color: var(--sp-text);
    letter-spacing: -0.3px;
    line-height: 1.05;
  }
  .sp-metric .s{
    font-size: 12px;
    color: var(--sp-muted2);
    margin-top: 8px;
    line-height: 1.25;
  }
  .sp-chip{
    display:inline-block;
    padding: 3px 8px;
    border-radius: 999px;
    border: 1px solid rgba(16,24,40,0.12);
    font-size: 11px;
    color: var(--sp-muted);
    background: rgba(255,255,255,0.92);
    margin-left: 8px;
    box-shadow: 0 8px 18px rgba(16,24,40,0.06);
  }

  /* Streamlit bordered containers (Dayparts mini-cards) */
  div[data-testid="stVerticalBlockBorderWrapper"]{
    border: 1px solid rgba(16,24,40,0.10) !important;
    background: linear-gradient(180deg, rgba(255,255,255,0.98) 0%, rgba(251,248,245,0.98) 100%) !important;
    border-radius: 18px !important;
    box-shadow: 0 12px 26px rgba(16,24,40,0.06) !important;
    padding: 10px 12px !important;
    transition: transform 160ms ease, box-shadow 160ms ease, border-color 160ms ease;
  }
  div[data-testid="stVerticalBlockBorderWrapper"]:hover{
    transform: translateY(-1px);
    box-shadow: 0 16px 34px rgba(16,24,40,0.08) !important;
    border-color: rgba(16,24,40,0.14) !important;
  }
  div[data-testid="stVerticalBlockBorderWrapper"] > div{ padding: 0 !important; }

  /* Plotly */
  .js-plotly-plot .plotly .modebar { opacity: 0.35; }
  .js-plotly-plot:hover .plotly .modebar { opacity: 0.85; }
  /* Extra breathing room around Plotly charts */
  .js-plotly-plot{
    margin-top: 6px;
  }

  /* Inputs: warm beige tint (SaaS tech aligned with Horeca palette) */
  [data-baseweb="input"] > div,
  [data-baseweb="select"] > div,
  [data-baseweb="textarea"]{
    border-radius: 14px !important;
    background: var(--sp-input-bg) !important;
    border: 1px solid rgba(16,24,40,0.14) !important;
    transition: background 140ms ease, border-color 140ms ease, box-shadow 140ms ease;
  }

  [data-baseweb="input"] input,
  [data-baseweb="textarea"] textarea{
    background: transparent !important;
  }

  [data-baseweb="input"] > div:hover,
  [data-baseweb="select"] > div:hover,
  [data-baseweb="textarea"]:hover{
    background: var(--sp-input-bg-hover) !important;
    border-color: rgba(16,24,40,0.18) !important;
  }

  [data-baseweb="input"] > div:focus-within,
  [data-baseweb="select"] > div:focus-within,
  [data-baseweb="textarea"]:focus-within{
    background: #ffffff !important;
    box-shadow: 0 0 0 3px rgba(184,149,129,0.22) !important;
    border-color: rgba(184,149,129,0.70) !important;
  }

  /* Buttons */
  .stDownloadButton button, .stButton button{
    border-radius: 14px !important;
    border: 1px solid rgba(16,24,40,0.14) !important;
    box-shadow: 0 10px 22px rgba(16,24,40,0.08) !important;
    transition: transform 160ms ease, box-shadow 160ms ease;
  }
  .stDownloadButton button:hover, .stButton button:hover{
    transform: translateY(-1px);
    box-shadow: 0 14px 30px rgba(16,24,40,0.11) !important;
  }
  .stDownloadButton button:focus, .stButton button:focus{
    outline: none !important;
    box-shadow: 0 0 0 3px rgba(184,149,129,0.22), 0 14px 30px rgba(16,24,40,0.11) !important;
  }
  /* Primary buttons (e.g., report downloads): dark like hero/logo box */
  .stDownloadButton button[kind="primary"],
  .stButton button[kind="primary"]{
    background: var(--sp-dark) !important;
    color: #ffffff !important;
    border-color: rgba(255,255,255,0.10) !important;
  }
  .stDownloadButton button[kind="primary"]:hover,
  .stButton button[kind="primary"]:hover{
    filter: brightness(1.06);
  }
  .stDownloadButton button[kind="primary"] *{
    color: #ffffff !important;
  }

  /* Inline chart help dot (no chevron) */
  .sp-chart-head{
    display: inline-flex;
    align-items: center;
    gap: 10px;
    margin: 0 0 2px 0;
  }
  .sp-chart-head-title{
    font-weight: 950;
    color: var(--sp-text);
    line-height: 1.2;
  }
  .sp-help-inline{
    position: relative;
    display: inline-block;
  }
  .sp-help-inline > summary{
    list-style: none;
    width: 30px;
    height: 30px;
    border-radius: 999px;
    border: 1px solid rgba(255,255,255,0.18);
    background: linear-gradient(180deg, rgba(165,138,125,0.98), rgba(145,119,107,0.98));
    color: #fff;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    font-weight: 900;
    font-size: 14px;
    line-height: 1;
    cursor: pointer;
    box-shadow: 0 8px 18px rgba(16,24,40,0.12);
    user-select: none;
    transition: transform 130ms ease, box-shadow 130ms ease, background 130ms ease;
  }
  .sp-help-inline > summary::-webkit-details-marker{
    display: none;
  }
  .sp-help-inline > summary:hover{
    transform: translateY(-1px);
    background: linear-gradient(180deg, rgba(174,147,134,1), rgba(153,127,114,1));
    box-shadow: 0 12px 22px rgba(16,24,40,0.15);
  }
  .sp-help-panel{
    position: absolute;
    top: 36px;
    left: 0;
    z-index: 20;
    width: min(360px, 62vw);
    border-radius: 14px;
    border: 1px solid rgba(16,24,40,0.12);
    background: linear-gradient(180deg, rgba(255,255,255,0.98), rgba(251,248,245,0.98));
    box-shadow: 0 14px 28px rgba(16,24,40,0.10);
    color: var(--sp-text);
    padding: 10px 12px;
    font-size: 13px;
    line-height: 1.45;
  }

  /* Top utility (language) */
  .sp-topbar{ display:flex; justify-content:flex-end; align-items:center; gap:12px; margin: 6px 0 14px 0; }
  .sp-topbar .stSelectbox{ max-width: 130px; }
  .sp-topbar [data-baseweb="select"] > div{
    border-radius: 14px !important;
    border: 1px solid rgba(16,24,40,0.14) !important;
    box-shadow: 0 10px 22px rgba(16,24,40,0.06) !important;
  }
</style>
        """,
        unsafe_allow_html=True,
    )


_css()


# ============================
# Inputs: dual (slider + number) SAFE (no session-state mutation after instantiate)
# ============================
def euro_dual_input(
    label: str,
    *,
    min_value: float,
    max_value: float,
    default_value: float,
    step: float,
    key: str,
    help_text: str = "",
) -> float:
    slider_key = f"{key}__slider"
    num_key = f"{key}__num"

    if slider_key not in st.session_state:
        st.session_state[slider_key] = float(default_value)
    if num_key not in st.session_state:
        st.session_state[num_key] = float(default_value)

    def _sync_from_slider():
        st.session_state[num_key] = float(st.session_state[slider_key])

    def _sync_from_num():
        st.session_state[slider_key] = float(st.session_state[num_key])

    c1, c2 = st.columns([1.35, 1.0])
    with c1:
        st.slider(
            label,
            min_value=float(min_value),
            max_value=float(max_value),
            step=float(step),
            key=slider_key,
            on_change=_sync_from_slider,
            help=help_text or None,
        )
    with c2:
        st.number_input(
            " ",
            min_value=float(min_value),
            max_value=float(max_value),
            step=float(step),
            key=num_key,
            label_visibility="collapsed",
            on_change=_sync_from_num,
            help=help_text or None,
        )

    return float(st.session_state[num_key])


def cost_block_pct_or_eur(
    title: str,
    *,
    bench_min: float,
    bench_max: float,
    bench_default: float,
    eur_max: float,
    key_prefix: str,
    help_text: str = "",
) -> Tuple[str, float, float]:
    help_attr = _html_escape(help_text or "")
    dot = f'<span class="sp-help-dot" title="{help_attr}">?</span>' if help_attr else ""
    st.markdown(
        f'<div class="sp-cost-title">{title}{dot}</div>',
        unsafe_allow_html=True,
    )

    mode_key = f"{key_prefix}_mode"
    if mode_key not in st.session_state:
        st.session_state[mode_key] = "pct"

    mode = st.radio(
        t("mode_label"),
        options=["pct", "eur"],
        format_func=lambda x: t("pct_on_rev") if x == "pct" else t("fixed_month"),
        horizontal=True,
        key=mode_key,
        label_visibility="collapsed",
        help=h("mode"),
    )

    if mode == "pct":
        pct_key = f"{key_prefix}_pct"
        if pct_key not in st.session_state:
            st.session_state[pct_key] = int(bench_default * 100)

        pct = (
            st.slider(
                " ",
                min_value=int(bench_min * 100),
                max_value=int(bench_max * 100),
                step=1,
                key=pct_key,
                label_visibility="collapsed",
                help=help_text or None,
            )
            / 100.0
        )
        eur = 0.0
    else:
        eur = euro_dual_input(
            title,
            min_value=0.0,
            max_value=float(eur_max),
            default_value=0.0,
            step=100.0,
            key=f"{key_prefix}_eur",
            help_text=help_text,
        )
        pct = 0.0

    return mode, float(pct), float(eur)


# ============================
# Load configs
# ============================
business_types = load_yaml("profiles/business_types.yaml")
dayparts_lib = load_yaml("profiles/dayparts.yaml")

# Ensure custom on top
if not any(bt.get("key") == "custom" for bt in business_types):
    business_types = [{
        "key": "custom",
        "label_it": "Custom",
        "label_en": "Custom",
        "default_dayparts": [],
        "benchmarks": {
            "cogs_pct": {"min": 0.15, "max": 0.50, "default": 0.30},
            "labor_pct": {"min": 0.18, "max": 0.50, "default": 0.30},
            "opex_pct": {"min": 0.05, "max": 0.30, "default": 0.12},
            "marketing_pct": {"min": 0.00, "max": 0.15, "default": 0.03},
            "fee_pct": {"min": 0.00, "max": 0.15, "default": 0.00},
        },
    }] + business_types
else:
    custom = [bt for bt in business_types if bt.get("key") == "custom"]
    others = [bt for bt in business_types if bt.get("key") != "custom"]
    business_types = custom + others

bt_by_key = {bt["key"]: bt for bt in business_types}
bt_keys = [bt["key"] for bt in business_types]
all_daypart_keys = [d["key"] for d in dayparts_lib]


# ============================
# Session defaults
# ============================
st.session_state.setdefault("lang", "IT")
st.session_state.setdefault("business_type_key", bt_keys[0])
st.session_state.setdefault("selected_dayparts", [])
st.session_state.setdefault("dayparts_customized", False)
st.session_state.setdefault("open_days", 30)
st.session_state.setdefault("use_hourly", False)
st.session_state.setdefault("enable_fee", False)
st.session_state.setdefault("inv_enable", False)
st.session_state.setdefault("fte_enable", False)
st.session_state.setdefault("ramp_enable", False)

st.session_state.setdefault("q1w", 25.0)
st.session_state.setdefault("q2w", 25.0)
st.session_state.setdefault("q3w", 25.0)
st.session_state.setdefault("q4w", 25.0)
st.session_state.setdefault("ramp_up_months", 0)
st.session_state.setdefault("ramp_up_floor", 0.65)
st.session_state.setdefault("fte_method", "m1")
st.session_state.setdefault("fte_hourly_cost", 18.0)
st.session_state.setdefault("fte_hours_per_fte", 1720.0)
st.session_state.setdefault("fte_use_y1", False)
st.session_state.setdefault("fte_target_inc", 30.0)


# ============================
# HERO
# ============================
logo_uri = _img_data_uri("assets/logo.png")
logo_html = f'<img src="{logo_uri}" class="sp-hero-logo" alt="StorePilot" />' if logo_uri else ""

# Short description
hero_desc_it = (
    "<strong>StorePilot</strong> è il simulatore economico che trasforma in pochi secondi ordini, scontrino medio e costi in "
    "<strong>ricavi, EBITDA e break-even</strong>."
)
hero_desc_en = (
    "<strong>StorePilot</strong> is an economic simulator for food retail locations. "
    "It turns orders, average ticket and costs into <strong>revenue, EBITDA and break-even</strong> in seconds."
)
hero_desc = hero_desc_it if st.session_state.get("lang", "IT") == "IT" else hero_desc_en

st.markdown(
    f"""
<div class="sp-hero">
  <div class="sp-hero-inner">
    {logo_html}
    <div class="sp-hero-copy">
      <p class="sub">Measure twice. Sign once.</p>
      <p class="payoff">Simula • Valuta • Decidi</p>
      <p style="margin-top:10px;font-size:15px;line-height:1.5;color:#eaeaea;font-weight:520;">
        {hero_desc}
      </p>
    </div>
  </div>
</div>
""",
    unsafe_allow_html=True,
)


# ============================
# TOP BAR: language
# ============================
st.markdown('<div class="sp-topbar">', unsafe_allow_html=True)
st.selectbox(
    t("language"),
    options=["IT", "EN"],
    key="lang",
    label_visibility="collapsed",
)
st.markdown('</div>', unsafe_allow_html=True)


# ============================
# GUIDE (accordion)
# ============================

st.markdown(f'<div class="sp-minihead">{icon("guide")} {t("guide")}</div>', unsafe_allow_html=True)
with st.expander(" ", expanded=False):
    if st.session_state["lang"] == "IT":
        st.markdown(
            """
<div class="sp-guide-hero">
  <p class="k">Come usare StorePilot</p>
  <p class="v">Inserisci pochi input operativi e ottieni in pochi secondi ricavi, EBITDA, break-even e valutazione finale.</p>
</div>
<div class="sp-guide-grid">
  <div class="sp-guide-card">
    <p class="h">Input minimi</p>
    <p class="b">Giorni apertura/mese, almeno 1 fascia con ordini+scontrino, costi base (COGS e Personale).</p>
  </div>
  <div class="sp-guide-card">
    <p class="h">Formula veloce</p>
    <p class="b"><strong>Ricavi mese</strong> = somma(fasce: ordini x ticket) x giorni apertura.</p>
  </div>
  <div class="sp-guide-card">
    <p class="h">Output chiave</p>
    <p class="b">Ricavi annui run-rate, EBITDA, break-even e confronto con benchmark del profilo scelto.</p>
  </div>
</div>
""",
            unsafe_allow_html=True,
        )
        st.markdown("#### Percorso consigliato")
        st.markdown(
            """
<div class="sp-guide-step"><span class="n">1</span><p class="t"><strong>Setup:</strong> scegli Tipologia locale e Giorni apertura/mese.</p></div>
<div class="sp-guide-step"><span class="n">2</span><p class="t"><strong>Fasce orarie:</strong> seleziona le fasce attive e compila ordini medi/giorno + scontrino medio.</p></div>
<div class="sp-guide-step"><span class="n">3</span><p class="t"><strong>Costi:</strong> per ogni voce scegli % su ricavi oppure euro mensili fissi.</p></div>
<div class="sp-guide-step"><span class="n">4</span><p class="t"><strong>Moduli opzionali:</strong> attiva Investimenti, Stagionalita/avviamento e FTE solo se utili al tuo caso.</p></div>
<div class="sp-guide-step"><span class="n">5</span><p class="t"><strong>Lettura output:</strong> guarda prima EBITDA% e break-even, poi approfondisci grafici e valutazione.</p></div>
<div class="sp-guide-step"><span class="n">6</span><p class="t"><strong>Report:</strong> invia via email per condivisione interna.</p></div>
<div class="sp-guide-tip"><strong>Tip pratico:</strong> se il break-even risulta troppo alto, controlla prima Occupancy e costi fissi mensili.</div>
""",
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            """
<div class="sp-guide-hero">
  <p class="k">How to use StorePilot</p>
  <p class="v">Enter a few operating inputs and quickly get revenue, EBITDA, break-even and final assessment.</p>
</div>
<div class="sp-guide-grid">
  <div class="sp-guide-card">
    <p class="h">Minimum inputs</p>
    <p class="b">Open days/month, at least 1 daypart with orders+ticket, core costs (COGS and Labor).</p>
  </div>
  <div class="sp-guide-card">
    <p class="h">Quick formula</p>
    <p class="b"><strong>Monthly revenue</strong> = sum(dayparts: orders x ticket) x open days.</p>
  </div>
  <div class="sp-guide-card">
    <p class="h">Core outputs</p>
    <p class="b">Annual run-rate revenue, EBITDA, break-even and benchmark gap vs selected profile.</p>
  </div>
</div>
""",
            unsafe_allow_html=True,
        )
        st.markdown("#### Recommended flow")
        st.markdown(
            """
<div class="sp-guide-step"><span class="n">1</span><p class="t"><strong>Setup:</strong> choose Business type and Open days/month.</p></div>
<div class="sp-guide-step"><span class="n">2</span><p class="t"><strong>Dayparts:</strong> select active dayparts and fill avg orders/day + avg ticket.</p></div>
<div class="sp-guide-step"><span class="n">3</span><p class="t"><strong>Costs:</strong> for each line choose % of revenue or fixed monthly euros.</p></div>
<div class="sp-guide-step"><span class="n">4</span><p class="t"><strong>Optional modules:</strong> enable Investments, Seasonality/ramp-up and FTE only when needed.</p></div>
<div class="sp-guide-step"><span class="n">5</span><p class="t"><strong>Read outputs:</strong> start from EBITDA% and break-even, then review charts and assessment.</p></div>
<div class="sp-guide-step"><span class="n">6</span><p class="t"><strong>Report:</strong> send via email for sharing.</p></div>
<div class="sp-guide-tip"><strong>Practical tip:</strong> if break-even is too high, check Occupancy and fixed monthly costs first.</div>
""",
            unsafe_allow_html=True,
        )

# --- Legenda & Glossario expander ---
st.markdown(
    f'<div class="sp-minihead">{icon("glossary")} ' + ("Legenda & Glossario" if st.session_state.get("lang", "IT") == "IT" else "Legend & Glossary") + '</div>',
    unsafe_allow_html=True,
)
with st.expander(" ", expanded=False):
    if st.session_state.get("lang", "IT") == "IT":
        st.markdown(
            """
<div class="sp-glossary-hero">
  <p class="k">Legenda rapida</p>
  <p class="v">Qui trovi il significato operativo delle metriche e come interpretarle in modo corretto.</p>
</div>
<div class="sp-glossary-grid">
  <div class="sp-glossary-card">
    <p class="h">Metriche core</p>
    <ul>
      <li><strong>Run-rate:</strong> proiezione a regime basata sugli input attuali.</li>
      <li><strong>Ricavi:</strong> valore netto IVA (net sales).</li>
      <li><strong>EBITDA:</strong> margine operativo 4-wall (prima di ammortamenti/interessi/imposte).</li>
      <li><strong>Break-even:</strong> ricavi annui (o ordini/giorno) per cui EBITDA = 0.</li>
      <li><strong>Y1:</strong> primo anno con stagionalita e/o avviamento se attivati.</li>
    </ul>
  </div>
  <div class="sp-glossary-card">
    <p class="h">Voci economiche</p>
    <ul>
      <li><strong>COGS:</strong> materie prime + packaging.</li>
      <li><strong>Labor:</strong> costo personale all-in.</li>
      <li><strong>OPEX:</strong> costi operativi non-food.</li>
      <li><strong>Occupancy:</strong> affitto + oneri, costo fisso rigido.</li>
      <li><strong>Fee:</strong> royalty/management fee/commissioni piattaforme.</li>
    </ul>
  </div>
  <div class="sp-glossary-card">
    <p class="h">Investimenti e staffing</p>
    <ul>
      <li><strong>ROI:</strong> EBITDA annuo / capitale investito.</li>
      <li><strong>Payback:</strong> mesi per recuperare il capitale investito.</li>
      <li><strong>FTE:</strong> Full-Time Equivalent (equivalente full-time).</li>
      <li><strong>% su ricavi:</strong> costo variabile proporzionale al fatturato.</li>
      <li><strong>€ fissi/mese:</strong> costo rigido non scalabile.</li>
    </ul>
  </div>
</div>
<div class="sp-glossary-note"><strong>Nota interpretativa:</strong> se ricavi bassi si combinano con costi fissi alti, il break-even sale rapidamente. La voce “Δ vs benchmark” indica lo scostamento rispetto al profilo selezionato.</div>
"""
            ,
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            """
<div class="sp-glossary-hero">
  <p class="k">Quick legend</p>
  <p class="v">Key definitions to read the simulator correctly and avoid interpretation errors.</p>
</div>
<div class="sp-glossary-grid">
  <div class="sp-glossary-card">
    <p class="h">Core metrics</p>
    <ul>
      <li><strong>Run-rate:</strong> steady-state projection from current inputs.</li>
      <li><strong>Revenue:</strong> net of VAT (net sales).</li>
      <li><strong>EBITDA:</strong> 4-wall operating margin.</li>
      <li><strong>Break-even:</strong> annual revenue (or orders/day) where EBITDA = 0.</li>
      <li><strong>Y1:</strong> first-year view with seasonality/ramp-up when enabled.</li>
    </ul>
  </div>
  <div class="sp-glossary-card">
    <p class="h">Economic lines</p>
    <ul>
      <li><strong>COGS:</strong> food + packaging.</li>
      <li><strong>Labor:</strong> all-in labor cost.</li>
      <li><strong>OPEX:</strong> non-food operating expenses.</li>
      <li><strong>Occupancy:</strong> rent + service charges, rigid fixed cost.</li>
      <li><strong>Fee:</strong> royalties/management/platform commissions.</li>
    </ul>
  </div>
  <div class="sp-glossary-card">
    <p class="h">Investment and staffing</p>
    <ul>
      <li><strong>ROI:</strong> annual EBITDA / cash invested.</li>
      <li><strong>Payback:</strong> months to recover invested cash.</li>
      <li><strong>FTE:</strong> Full-Time Equivalent.</li>
      <li><strong>% of revenue:</strong> variable cost mode.</li>
      <li><strong>Fixed monthly €:</strong> rigid cost mode.</li>
    </ul>
  </div>
</div>
<div class="sp-glossary-note"><strong>Interpretation note:</strong> low revenue plus high fixed costs pushes break-even up quickly. “Δ vs benchmark” measures the gap versus selected profile defaults.</div>
"""
            ,
            unsafe_allow_html=True,
        )


# ============================
# SETUP (card)
# ============================
with st.container(border=True):
    st.markdown(f'<div class="sp-title">{icon("setup")} {t("setup")}</div>', unsafe_allow_html=True)

    c1, c2, c3 = st.columns([1.2, 0.8, 0.8])

    with c1:
        st.selectbox(
            t("business_type"),
            options=bt_keys,
            format_func=lambda k: label_business(bt_by_key[k]),
            key="business_type_key",
            help=h("business_type"),
            on_change=_on_business_type_change,
        )
    with c2:
        st.number_input(
            t("open_days"),
            min_value=1,
            max_value=31,
            step=1,
            key="open_days",
            help=h("open_days"),
        )
    with c3:
        st.checkbox(t("hourly_opt"), key="use_hourly", help=h("hourly_opt"))

    bt = bt_by_key[st.session_state["business_type_key"]]
    bench = bt.get("benchmarks", {})

    c_mn, c_mx, c_df = _bench_range(bench, "cogs_pct")
    l_mn, l_mx, l_df = _bench_range(bench, "labor_pct")
    o_mn, o_mx, o_df = _bench_range(bench, "opex_pct")
    m_mn, m_mx, m_df = _bench_range(bench, "marketing_pct")
    f_mn, f_mx, f_df = _bench_range(bench, "fee_pct")

    st.caption(
        f'{t("legend")}: '
        f'COGS {int(c_mn*100)}–{int(c_mx*100)}% | '
        f'Labor {int(l_mn*100)}–{int(l_mx*100)}% | '
        f'OPEX {int(o_mn*100)}–{int(o_mx*100)}% | '
        f'MKT {int(m_mn*100)}–{int(m_mx*100)}%'
    )


# ============================
# DAYPARTS (card)
# ============================
with st.container(border=True):
    st.markdown(f'<div class="sp-title">{icon("dayparts")} {t("dayparts")}</div>', unsafe_allow_html=True)

    # Apply profile defaults on first load (only if user has not customized yet)
    if (not st.session_state.get("selected_dayparts")) and (not st.session_state.get("dayparts_customized", False)):
        _apply_profile_dayparts(force=True)

    # Optional: let the user re-apply profile defaults at any time (right-aligned)
    reset_label = (
        "Ripristina fasce del profilo" if st.session_state.get("lang", "IT") == "IT" else "Reset profile dayparts"
    )

    spacer, btn_col = st.columns([0.78, 0.22])
    with btn_col:
        # Important: avoid mutating the multiselect key after instantiation.
        # We set a flag and rerun; defaults will be applied BEFORE rendering the widget.
        if st.button(reset_label, type="primary", use_container_width=True):
            st.session_state["_dayparts_reset_requested"] = True
            st.rerun()

    if st.session_state.get("_dayparts_reset_requested", False):
        _apply_profile_dayparts(force=True)
        st.session_state["_dayparts_reset_requested"] = False

    # Ensure the widget has an initial value
    st.session_state.setdefault(DAYPARTS_WIDGET_KEY, st.session_state.get("selected_dayparts", []))

    _ = st.multiselect(
        t("dayparts_select"),
        options=all_daypart_keys,
        format_func=lambda k: label_daypart(next((d for d in dayparts_lib if d.get("key") == k), {"key": k})),
        key=DAYPARTS_WIDGET_KEY,
        help=h("dayparts_select"),
        on_change=_on_dayparts_widget_change,
    )

    # Canonical selection (always use this downstream)
    selected = list(st.session_state.get("selected_dayparts", []) or [])

    daypart_inputs: List[Any] = []

    if not selected:
        premium_info(t("need_dayparts"))
    else:
        # Render one mini-card per selected daypart
        for dp_key in selected:
            dp_obj = next((d for d in dayparts_lib if d.get("key") == dp_key), {"key": dp_key, "label_it": dp_key, "label_en": dp_key})
            dp_label = label_daypart(dp_obj)

            with st.container(border=True):
                st.markdown(f"<div style='font-weight:900; margin-bottom:6px;'>{dp_label}</div>", unsafe_allow_html=True)

                c1, c2 = st.columns(2)
                with c1:
                    orders = euro_dual_input(
                        t("orders_day"),
                        min_value=0.0,
                        max_value=2000.0,
                        default_value=float(st.session_state.get(f"dp_{dp_key}_orders", 0.0) or 0.0),
                        step=1.0,
                        key=f"dp_{dp_key}_orders",
                        help_text=h("orders_day"),
                    )
                with c2:
                    ticket = euro_dual_input(
                        t("ticket"),
                        min_value=0.0,
                        max_value=250.0,
                        default_value=float(st.session_state.get(f"dp_{dp_key}_ticket", 0.0) or 0.0),
                        step=0.5,
                        key=f"dp_{dp_key}_ticket",
                        help_text=h("ticket"),
                    )

                start_t: Optional[str] = None
                end_t: Optional[str] = None

                if bool(st.session_state.get("use_hourly", False)):
                    tc1, tc2 = st.columns(2)
                    with tc1:
                        start_t = st.text_input(
                            t("start_time"),
                            value=str(st.session_state.get(f"dp_{dp_key}_start", "")),
                            key=f"dp_{dp_key}_start",
                            help=h("start_time"),
                            placeholder="10:00",
                        ).strip() or None
                    with tc2:
                        end_t = st.text_input(
                            t("end_time"),
                            value=str(st.session_state.get(f"dp_{dp_key}_end", "")),
                            key=f"dp_{dp_key}_end",
                            help=h("end_time"),
                            placeholder="14:00",
                        ).strip() or None

                # Build engine input (robust to different DaypartInput signatures)
                # The engine reads `dp.orders_per_day` and `dp.ticket_avg`.
                dp_orders = float(orders)
                dp_ticket = float(ticket)

                def _make_daypart_input() -> Any:
                    """Create a DaypartInput instance (or a compatible fallback) regardless of signature."""
                    # Map of our canonical values
                    values = {
                        "key": dp_key,
                        "orders_per_day": dp_orders,
                        "ticket_avg": dp_ticket,
                        "start_time": start_t,
                        "end_time": end_t,
                    }

                    # Try to instantiate DaypartInput by inspecting its signature
                    try:
                        sig = inspect.signature(DaypartInput)
                        params = [p for p in sig.parameters.values() if p.name != "self"]
                        kwargs: Dict[str, Any] = {}

                        # Common aliases that might appear in the constructor
                        aliases = {
                            "key": ["key", "daypart_key", "name", "code"],
                            "orders_per_day": ["orders_per_day", "orders", "avg_orders", "orders_day"],
                            "ticket_avg": ["ticket_avg", "avg_ticket", "ticket", "avg_scontrino"],
                            "start_time": ["start_time", "start", "from_time"],
                            "end_time": ["end_time", "end", "to_time"],
                        }

                        for p in params:
                            pname = p.name
                            # find which canonical field this param corresponds to
                            canonical = None
                            for canon, names in aliases.items():
                                if pname in names:
                                    canonical = canon
                                    break

                            if canonical is not None:
                                kwargs[pname] = values[canonical]

                        # If we collected something meaningful, try kwargs
                        if kwargs:
                            try:
                                return DaypartInput(**kwargs)
                            except TypeError:
                                pass

                        # Positional fallback: key, orders, ticket, start, end (only if constructor supports)
                        try:
                            return DaypartInput(values["key"], values["orders_per_day"], values["ticket_avg"], values["start_time"], values["end_time"])  # type: ignore
                        except TypeError:
                            try:
                                return DaypartInput(values["key"], values["orders_per_day"], values["ticket_avg"])  # type: ignore
                            except TypeError:
                                return DaypartInput(values["key"], values["orders_per_day"], values["ticket_avg"])  # last attempt
                    except Exception:
                        # Ultimate safe fallback
                        return SimpleNamespace(
                            key=values["key"],
                            orders_per_day=values["orders_per_day"],
                            ticket_avg=values["ticket_avg"],
                            start_time=values["start_time"],
                            end_time=values["end_time"],
                        )

                def _normalize_daypart(dp_obj: Any) -> Any:
                    """Ensure required attributes exist and are not None. Fallback to SimpleNamespace if needed."""
                    key_val = dp_key
                    orders_val = dp_orders
                    ticket_val = dp_ticket

                    # Read current values if present
                    for k_attr in ("key", "daypart_key", "name"):
                        if hasattr(dp_obj, k_attr) and getattr(dp_obj, k_attr) is not None:
                            key_val = getattr(dp_obj, k_attr)
                            break

                    if hasattr(dp_obj, "orders_per_day") and getattr(dp_obj, "orders_per_day") is not None:
                        orders_val = float(getattr(dp_obj, "orders_per_day"))

                    # Ticket: engine expects ticket_avg
                    if hasattr(dp_obj, "ticket_avg") and getattr(dp_obj, "ticket_avg") is not None:
                        ticket_val = float(getattr(dp_obj, "ticket_avg"))
                    else:
                        # Try other possible attribute names
                        for t_attr in ("avg_ticket", "ticket"):
                            if hasattr(dp_obj, t_attr) and getattr(dp_obj, t_attr) is not None:
                                ticket_val = float(getattr(dp_obj, t_attr))
                                break

                    # If we can, enforce attributes on the object; otherwise create a compatible fallback
                    try:
                        if hasattr(dp_obj, "key"):
                            setattr(dp_obj, "key", key_val)
                        if hasattr(dp_obj, "orders_per_day"):
                            setattr(dp_obj, "orders_per_day", float(orders_val))
                        # Always ensure ticket_avg exists for the engine
                        setattr(dp_obj, "ticket_avg", float(ticket_val))
                        # Times are optional
                        if hasattr(dp_obj, "start_time"):
                            setattr(dp_obj, "start_time", start_t)
                        if hasattr(dp_obj, "end_time"):
                            setattr(dp_obj, "end_time", end_t)
                        return dp_obj
                    except Exception:
                        return SimpleNamespace(
                            key=key_val,
                            orders_per_day=float(orders_val),
                            ticket_avg=float(ticket_val),
                            start_time=start_t,
                            end_time=end_t,
                        )

                dp_obj = _make_daypart_input()
                daypart_inputs.append(_normalize_daypart(dp_obj))


# ============================
# COSTS (card)
# ============================
with st.container(border=True):
    st.markdown(f'<div class="sp-title">{icon("costs")} {t("costs")}</div>', unsafe_allow_html=True)
    st.markdown(
        f'<div class="sp-subtitle">'
        f'{"Imposta i costi come % su ricavi o come fisso mensile. Occupancy = affitto + oneri." if st.session_state["lang"]=="IT" else "Set costs as % of revenue or fixed monthly. Occupancy = rent + service charges."}'
        f'</div>',
        unsafe_allow_html=True,
    )

    left, right = st.columns(2)

    with left:
        cogs_mode, cogs_pct, cogs_eur = cost_block_pct_or_eur(
            t("cogs"),
            bench_min=c_mn,
            bench_max=c_mx,
            bench_default=c_df,
            eur_max=120_000.0,
            key_prefix="cogs",
            help_text=h("cogs"),
        )
        st.markdown('<div class="sp-divider"></div>', unsafe_allow_html=True)
        labor_mode, labor_pct, labor_eur = cost_block_pct_or_eur(
            t("labor"),
            bench_min=l_mn,
            bench_max=l_mx,
            bench_default=l_df,
            eur_max=160_000.0,
            key_prefix="labor",
            help_text=h("labor"),
        )

    with right:
        opex_mode, opex_pct, opex_eur = cost_block_pct_or_eur(
            t("opex"),
            bench_min=o_mn,
            bench_max=o_mx,
            bench_default=o_df,
            eur_max=160_000.0,
            key_prefix="opex",
            help_text=h("opex"),
        )
        st.markdown('<div class="sp-divider"></div>', unsafe_allow_html=True)
        marketing_mode, marketing_pct, marketing_eur = cost_block_pct_or_eur(
            t("mkt"),
            bench_min=m_mn,
            bench_max=m_mx,
            bench_default=m_df,
            eur_max=80_000.0,
            key_prefix="mkt",
            help_text=h("mkt"),
        )

    st.markdown('<div class="sp-divider"></div>', unsafe_allow_html=True)

    fee_l, occ_r = st.columns([0.9, 1.1])

    with fee_l:
        st.markdown(
            f'<div style="font-weight:900; margin-bottom:6px;">{icon("fee")} {t("fee_title")}</div>',
            unsafe_allow_html=True,
        )
        st.checkbox(t("fee_enable"), key="enable_fee", help=h("fee_enable"))
        if bool(st.session_state.get("enable_fee", False)):
            fee_mode, fee_pct, fee_eur = cost_block_pct_or_eur(
                t("fee"),
                bench_min=f_mn,
                bench_max=f_mx,
                bench_default=max(f_df, 0.0),
                eur_max=80_000.0,
                key_prefix="fee",
                help_text=h("fee"),
            )
        else:
            fee_mode, fee_pct, fee_eur = "pct", 0.0, 0.0

    with occ_r:
        st.markdown(
            f'<div style="font-weight:900; margin-bottom:6px;">{icon("occupancy")} {t("occupancy")}</div>',
            unsafe_allow_html=True,
        )
        rent_fixed = euro_dual_input(
            t("rent"),
            min_value=0.0,
            max_value=80_000.0,
            default_value=float(st.session_state.get("rent_fixed__num", 0.0) or 0.0),
            step=100.0,
            key="rent_fixed",
            help_text=h("rent"),
        )
        service_charges = euro_dual_input(
            t("service"),
            min_value=0.0,
            max_value=40_000.0,
            default_value=float(st.session_state.get("service_charges__num", 0.0) or 0.0),
            step=50.0,
            key="service_charges",
            help_text=h("service"),
        )


# ============================
# OPTIONALS
# ============================
# Investimenti
minihead_with_help(
    "invest",
    t("invest"),
    (
        "ROI: rapporto tra EBITDA annuo e capitale investito. "
        "Payback: mesi necessari a recuperare il capitale investito tramite EBITDA."
        if st.session_state.get("lang", "IT") == "IT"
        else
        "ROI: ratio between annual EBITDA and cash invested. "
        "Payback: months needed to recover cash invested through EBITDA."
    ),
)
with st.expander(" ", expanded=False):
    st.checkbox(t("invest_enable"), key="inv_enable", help=h("invest_enable"))
    if bool(st.session_state.get("inv_enable", False)):
        st.caption(
            "Suggerimento: usa valori prudenziali e considera che ROI/payback qui sono una stima operativa (proxy su EBITDA)."
            if st.session_state.get("lang", "IT") == "IT"
            else "Tip: use conservative values; ROI/payback here are operational estimates (EBITDA proxy)."
        )
    if bool(st.session_state.get("inv_enable", False)):
        capex = euro_dual_input(
            t("capex"),
            min_value=0.0,
            max_value=500_000.0,
            default_value=float(st.session_state.get("capex__num", 0.0) or 0.0),
            step=500.0,
            key="capex",
            help_text=h("capex"),
        )
        deposits = euro_dual_input(
            t("deposits"),
            min_value=0.0,
            max_value=200_000.0,
            default_value=float(st.session_state.get("deposits__num", 0.0) or 0.0),
            step=500.0,
            key="deposits",
            help_text=h("deposits"),
        )
        immobilizations = euro_dual_input(
            t("immobilizations"),
            min_value=0.0,
            max_value=500_000.0,
            default_value=float(st.session_state.get("immobilizations__num", 0.0) or 0.0),
            step=500.0,
            key="immobilizations",
            help_text=h("immobilizations"),
        )
        guarantees = euro_dual_input(
            t("guarantees"),
            min_value=0.0,
            max_value=500_000.0,
            default_value=float(st.session_state.get("guarantees__num", 0.0) or 0.0),
            step=500.0,
            key="guarantees",
            help_text=h("guarantees"),
        )
    else:
        capex = deposits = immobilizations = guarantees = 0.0

# Stagionalità & avviamento
minihead_with_help(
    "seasonality",
    t("seasonality"),
    (
        "La stagionalità distribuisce i ricavi su Q1-Q4. L'avviamento riduce i primi mesi e sale gradualmente fino al 100%."
        if st.session_state.get("lang", "IT") == "IT"
        else
        "Seasonality distributes revenue across Q1-Q4. Ramp-up lowers first months and gradually reaches 100%."
    ),
)
with st.expander(" ", expanded=False):
    st.checkbox(t("season_enable"), key="ramp_enable", help=h("season_enable"))
    if bool(st.session_state.get("ramp_enable", False)):
        st.caption(
            "Esempio: Q2/Q3 più alti per località turistiche; ramp-up 3-6 mesi per nuove aperture."
            if st.session_state.get("lang", "IT") == "IT"
            else "Example: higher Q2/Q3 for tourist areas; 3-6 ramp-up months for new openings."
        )
        q1, q2, q3, q4 = st.columns(4)
        with q1:
            st.number_input(t("q1"), min_value=0.0, max_value=100.0, step=1.0, key="q1w", help=h("q"))
        with q2:
            st.number_input(t("q2"), min_value=0.0, max_value=100.0, step=1.0, key="q2w", help=h("q"))
        with q3:
            st.number_input(t("q3"), min_value=0.0, max_value=100.0, step=1.0, key="q3w", help=h("q"))
        with q4:
            st.number_input(t("q4"), min_value=0.0, max_value=100.0, step=1.0, key="q4w", help=h("q"))
        q_total = (
            float(st.session_state.get("q1w", 0.0))
            + float(st.session_state.get("q2w", 0.0))
            + float(st.session_state.get("q3w", 0.0))
            + float(st.session_state.get("q4w", 0.0))
        )
        if abs(q_total - 100.0) > 0.01:
            premium_notice(
                f"{'Somma pesi trimestrali' if st.session_state.get('lang','IT')=='IT' else 'Quarter weight total'}: {q_total:.1f}%. "
                + ("Consigliato 100%." if st.session_state.get("lang", "IT") == "IT" else "Recommended 100%."),
                level="warn",
            )
        else:
            premium_notice(
                f"{'Somma pesi trimestrali' if st.session_state.get('lang','IT')=='IT' else 'Quarter weight total'}: 100%.",
                level="ok",
            )
        st.slider(t("ramp_months"), 0, 12, step=1, key="ramp_up_months", help=h("ramp_months"))
        st.slider(t("ramp_floor"), 0.20, 1.00, step=0.05, key="ramp_up_floor", help=h("ramp_floor"))

# FTE
minihead_with_help(
    "fte",
    t("fte"),
    (
        "FTE (Full-Time Equivalent): numero di risorse a tempo pieno equivalenti necessarie per sostenere il modello operativo."
        if st.session_state.get("lang", "IT") == "IT"
        else
        "FTE (Full-Time Equivalent): equivalent number of full-time staff needed to support the operating model."
    ),
)
with st.expander(" ", expanded=False):
    st.checkbox(t("fte_enable"), key="fte_enable", help=h("fte_enable"))
    if bool(st.session_state.get("fte_enable", False)):
        st.session_state.setdefault("fte_method", "m1")
        st.radio(
            t("fte_method"),
            options=["m1", "m2"],
            format_func=lambda x: t("fte_m1") if x == "m1" else t("fte_m2"),
            horizontal=True,
            key="fte_method",
            help=h("fte_method"),
        )
        if st.session_state.get("lang", "IT") == "IT":
            if st.session_state.get("fte_method") == "m1":
                premium_info("Metodo m1: usa il costo personale già calcolato nel modello economico e lo converte in ore/FTE.")
            else:
                premium_info("Metodo m2: parte da una % target di personale sui ricavi; utile in fase previsionale quando il costo lavoro reale non è definito.")
        else:
            if st.session_state.get("fte_method") == "m1":
                premium_info("m1 method: uses labor cost already computed in the model, then converts it to hours/FTE.")
            else:
                premium_info("m2 method: starts from a target labor % on revenue; useful in early forecasting when actual labor cost is not fixed.")

        st.markdown(f"**{t('hourly_cost')}**")
        slider_with_free_input(
            label=t("hourly_cost"),
            key="fte_hourly_cost",
            min_value=8.0,
            max_value=35.0,
            step=0.5,
            default_value=18.0,
            help_text=h("hourly_cost"),
        )

        st.markdown(f"**{t('hours_per_fte')}**")
        presets_it = {
            "Part-time prevalente (1.560 h)": 1560.0,
            "Standard retail Italia (1.720 h)": 1720.0,
            "Produttività alta (1.900 h)": 1900.0,
            "Custom": None,
        }
        presets_en = {
            "Mostly part-time (1,560 h)": 1560.0,
            "Italy retail standard (1,720 h)": 1720.0,
            "High productivity (1,900 h)": 1900.0,
            "Custom": None,
        }
        presets = presets_it if st.session_state.get("lang", "IT") == "IT" else presets_en
        preset_choice = st.selectbox(
            "Preset ore/FTE" if st.session_state.get("lang", "IT") == "IT" else "Hours/FTE preset",
            options=list(presets.keys()),
            index=1,
            key="fte_hours_preset",
        )
        preset_val = presets.get(preset_choice)
        if isinstance(preset_val, (int, float)):
            st.session_state["fte_hours_per_fte"] = float(preset_val)

        slider_with_free_input(
            label=t("hours_per_fte"),
            key="fte_hours_per_fte",
            min_value=1200.0,
            max_value=2100.0,
            step=10.0,
            default_value=float(st.session_state.get("fte_hours_per_fte", 1720.0)),
            help_text=h("hours_per_fte"),
        )
        st.caption(
            "Regola pratica: ore annue/FTE = ore contrattuali teoriche - ferie/festività/assenze."
            if st.session_state.get("lang", "IT") == "IT"
            else "Rule of thumb: annual hours/FTE = theoretical contractual hours - holidays/vacation/absences."
        )

        st.checkbox(t("use_y1"), key="fte_use_y1", help=h("use_y1"))
        if st.session_state.get("fte_method") == "m2":
            st.markdown(f"**{t('target_labor')}**")
            slider_with_free_input(
                label=t("target_labor"),
                key="fte_target_inc",
                min_value=0.0,
                max_value=60.0,
                step=1.0,
                default_value=30.0,
                help_text=h("target_labor"),
            )


# ============================
# COMPUTE
# ============================
seasonality_active = bool(st.session_state.get("ramp_enable", False))

results = calculate_financials(
    dayparts=daypart_inputs,
    open_days_per_month=int(st.session_state["open_days"]),

    cogs_mode=cogs_mode,
    labor_mode=labor_mode,
    opex_mode=opex_mode,
    marketing_mode=marketing_mode,
    fee_mode=fee_mode,

    cogs_pct=float(cogs_pct),
    labor_pct=float(labor_pct),
    opex_pct=float(opex_pct),
    marketing_pct=float(marketing_pct),
    fee_pct=float(fee_pct),

    cogs_eur=float(cogs_eur),
    labor_eur=float(labor_eur),
    opex_eur=float(opex_eur),
    marketing_eur=float(marketing_eur),
    fee_eur=float(fee_eur),

    rent_fixed_month=float(rent_fixed),
    service_charges_month=float(service_charges),

    capex=float(capex) if bool(st.session_state.get("inv_enable", False)) else 0.0,
    deposits=float(deposits) if bool(st.session_state.get("inv_enable", False)) else 0.0,
    immobilizations=float(immobilizations) if bool(st.session_state.get("inv_enable", False)) else 0.0,
    guarantees=float(guarantees) if bool(st.session_state.get("inv_enable", False)) else 0.0,

    quarter_weights=[
        float(st.session_state.get("q1w", 25.0)),
        float(st.session_state.get("q2w", 25.0)),
        float(st.session_state.get("q3w", 25.0)),
        float(st.session_state.get("q4w", 25.0)),
    ] if seasonality_active else None,
    ramp_up_months=int(st.session_state.get("ramp_up_months", 0)) if seasonality_active else 0,
    ramp_up_floor=float(st.session_state.get("ramp_up_floor", 0.65)) if seasonality_active else 0.65,
)

fe = evaluate_feasibility(results)
# ============================
# REPORTS (build bytes)
# ============================
_can_build_reports = bool(daypart_inputs) and float(results.get("revenue_annual_runrate", 0.0) or 0.0) > 0

_reports_payload: Dict[str, Any] = {
    "xlsx_bytes": None,
    "xlsx_err": None,
    "pdf_bytes": None,
    "pdf_err": None,
}

if _can_build_reports:
    _report_dayparts: List[Dict[str, Any]] = []
    _open_days_report = int(st.session_state.get("open_days", 30) or 30)
    for _dp in daypart_inputs:
        try:
            _k = str(getattr(_dp, "key", "") or "")
            _dp_obj = next(
                (d for d in dayparts_lib if d.get("key") == _k),
                {"key": _k, "label_it": _k, "label_en": _k},
            )
            _label = label_daypart(_dp_obj)
            _orders = float(getattr(_dp, "orders_per_day", 0.0) or 0.0)
            _ticket = float(getattr(_dp, "ticket_avg", 0.0) or 0.0)
            _monthly = max(0.0, _orders * _ticket * _open_days_report)
            if _monthly > 0:
                _report_dayparts.append({"label": _label, "monthly_revenue": _monthly})
        except Exception:
            pass

    _report_inputs: Dict[str, Any] = {
        "language": st.session_state.get("lang", "IT"),
        "business_label": label_business(bt_by_key[st.session_state["business_type_key"]]),
        "open_days": int(st.session_state.get("open_days", 30) or 30),
        "capex": float(capex) if bool(st.session_state.get("inv_enable", False)) else 0.0,
        "deposits": float(deposits) if bool(st.session_state.get("inv_enable", False)) else 0.0,
        "immobilizations": float(immobilizations) if bool(st.session_state.get("inv_enable", False)) else 0.0,
        "guarantees": float(guarantees) if bool(st.session_state.get("inv_enable", False)) else 0.0,
        "seasonality_enabled": bool(st.session_state.get("ramp_enable", False)),
        "ramp_up_months": int(st.session_state.get("ramp_up_months", 0) or 0),
        "ramp_up_floor": float(st.session_state.get("ramp_up_floor", 0.65) or 0.65),
        "fte_enabled": bool(st.session_state.get("fte_enable", False)),
        "fte_method": str(st.session_state.get("fte_method", "m1")),
        "daypart_breakdown": _report_dayparts,
    }
    try:
        _reports_payload = _build_reports_cached(
            _report_inputs,
            results,
            fe,
            st.session_state.get("lang", "IT"),
        )
    except Exception as _e:
        _reports_payload = {
            "xlsx_bytes": None,
            "xlsx_err": str(_e),
            "pdf_bytes": None,
            "pdf_err": str(_e),
        }


# ============================
# KPI deltas vs benchmark
# ============================

def _actual_pct(cost_annual: float, revenue_annual: float) -> float:
    if revenue_annual <= 0:
        return 0.0
    return max(0.0, float(cost_annual) / float(revenue_annual))


rev = float(results.get("revenue_annual_runrate", 0.0) or 0.0)

actual_cogs_pct = _actual_pct(float(results.get("cogs_annual_runrate", 0.0) or 0.0), rev)
actual_labor_pct = _actual_pct(float(results.get("labor_annual_runrate", 0.0) or 0.0), rev)
actual_opex_pct = _actual_pct(float(results.get("opex_annual_runrate", 0.0) or 0.0), rev)
actual_mkt_pct = _actual_pct(float(results.get("marketing_annual_runrate", 0.0) or 0.0), rev)
actual_fee_pct = _actual_pct(float(results.get("fee_annual_runrate", 0.0) or 0.0), rev)

# Benchmark deltas (percentage points)
bench_cogs = float(c_df)
bench_labor = float(l_df)
bench_opex = float(o_df)
bench_mkt = float(m_df)


def _num(x: Any, default: float = 0.0) -> float:
    try:
        if x is None:
            return float(default)
        return float(x)
    except Exception:
        return float(default)


def _fmt_months(v: Any) -> str:
    x = _num(v, -1.0)
    if x <= 0:
        return t("na")
    suffix = "mesi" if st.session_state.get("lang", "IT") == "IT" else "months"
    return f"{x:.1f} {suffix}"


seasonality_enabled = bool(st.session_state.get("ramp_enable", False))
invest_enabled = bool(st.session_state.get("inv_enable", False))
fte_enabled = bool(st.session_state.get("fte_enable", False))

cash_invested = _num(results.get("cash_invested"), 0.0)
roi_run = results.get("roi_annual")
payback_run = results.get("payback_months")
roi_y1 = results.get("roi_annual_y1")
payback_y1 = results.get("payback_months_y1")

y1_rev = _num(results.get("revenue_annual_y1"), 0.0)
y1_ebitda = _num(results.get("ebitda_annual_y1"), 0.0)
y1_ebitda_pct = (y1_ebitda / y1_rev) if y1_rev > 0 else None

# FTE output model
fte_method = str(st.session_state.get("fte_method", "m1"))
fte_use_y1 = bool(st.session_state.get("fte_use_y1", False))
fte_hourly_cost = _num(st.session_state.get("fte_hourly_cost"), 0.0)
fte_hours_per_fte = max(1.0, _num(st.session_state.get("fte_hours_per_fte"), 1720.0))
fte_target_inc = _num(st.session_state.get("fte_target_inc"), 0.0) / 100.0

if fte_use_y1 and seasonality_enabled:
    fte_revenue_base = y1_rev
    fte_labor_base_annual_m1 = _num(results.get("labor_annual_y1"), 0.0)
else:
    fte_revenue_base = _num(results.get("revenue_annual_runrate"), 0.0)
    fte_labor_base_annual_m1 = _num(results.get("labor_annual_runrate"), 0.0)

if fte_method == "m2":
    fte_labor_base_annual = max(0.0, fte_revenue_base * fte_target_inc)
else:
    fte_labor_base_annual = max(0.0, fte_labor_base_annual_m1)

fte_labor_base_month = fte_labor_base_annual / 12.0
fte_labor_hours_annual = (fte_labor_base_annual / fte_hourly_cost) if fte_hourly_cost > 0 else 0.0
fte_total = (fte_labor_hours_annual / fte_hours_per_fte) if fte_hours_per_fte > 0 else 0.0


def _hours_between_local(start_s: Any, end_s: Any) -> Optional[float]:
    try:
        s = str(start_s or "").strip()
        e = str(end_s or "").strip()
        if len(s) != 5 or len(e) != 5 or s[2] != ":" or e[2] != ":":
            return None
        sh, sm = int(s[:2]), int(s[3:])
        eh, em = int(e[:2]), int(e[3:])
        if not (0 <= sh <= 23 and 0 <= eh <= 23 and 0 <= sm <= 59 and 0 <= em <= 59):
            return None
        smin = sh * 60 + sm
        emin = eh * 60 + em
        if emin < smin:
            emin += 24 * 60
        if emin == smin:
            return None
        return (emin - smin) / 60.0
    except Exception:
        return None


fte_daypart_rows: List[Dict[str, Any]] = []
open_days_val = int(st.session_state.get("open_days", 30) or 30)
tmp_rows: List[Dict[str, Any]] = []
for dp in daypart_inputs:
    try:
        dp_key = str(getattr(dp, "key", "") or "")
        dp_obj = next(
            (d for d in dayparts_lib if d.get("key") == dp_key),
            {"key": dp_key, "label_it": dp_key, "label_en": dp_key},
        )
        dp_label = label_daypart(dp_obj)
        dp_month_rev = (
            _num(getattr(dp, "orders_per_day", 0.0))
            * _num(getattr(dp, "ticket_avg", 0.0))
            * float(open_days_val)
        )
        dp_hours_day = _hours_between_local(getattr(dp, "start_time", None), getattr(dp, "end_time", None))
        tmp_rows.append({
            "label": dp_label,
            "rev_month": max(0.0, dp_month_rev),
            "hours_day": dp_hours_day,
        })
    except Exception:
        pass

tot_month_rev = sum(r["rev_month"] for r in tmp_rows)
row_count = len(tmp_rows)
for r in tmp_rows:
    if row_count <= 0:
        break
    share = (r["rev_month"] / tot_month_rev) if tot_month_rev > 0 else (1.0 / row_count)
    labor_cost_annual_alloc = fte_labor_base_annual * share
    labor_hours_annual_alloc = (labor_cost_annual_alloc / fte_hourly_cost) if fte_hourly_cost > 0 else 0.0
    fte_alloc = (labor_hours_annual_alloc / fte_hours_per_fte) if fte_hours_per_fte > 0 else 0.0
    avg_on_shift = None
    if isinstance(r["hours_day"], (int, float)) and r["hours_day"] and open_days_val > 0:
        annual_slot_hours = float(r["hours_day"]) * float(open_days_val) * 12.0
        if annual_slot_hours > 0:
            avg_on_shift = labor_hours_annual_alloc / annual_slot_hours
    fte_daypart_rows.append({
        "label": r["label"],
        "share": share,
        "cost_annual": labor_cost_annual_alloc,
        "hours_annual": labor_hours_annual_alloc,
        "fte": fte_alloc,
        "avg_on_shift": avg_on_shift,
    })


# ============================
# RESULTS (cards)
# ============================

st.markdown('<div class="sp-section-divider"></div>', unsafe_allow_html=True)

with st.container(border=True):
    st.markdown(f'<div class="sp-title">{icon("results")} {t("results")}</div>', unsafe_allow_html=True)

    # Run-rate KPIs
    rev_run = float(
        results.get(
            "revenue_annual_runrate",
            results.get("revenue_annual_run_rate", results.get("revenue_year", 0.0)),
        )
        or 0.0
    )

    ebitda = float(
        results.get(
            "ebitda_annual_runrate",
            results.get("ebitda_annual_run_rate", results.get("ebitda_year", 0.0)),
        )
        or 0.0
    )

    # EBITDA% coherently from run-rate EBITDA / run-rate revenue
    ebitda_pct = (ebitda / rev_run) if rev_run > 0 else None

    be_rev = results.get("break_even_revenue_annual", None)
    be_orders = results.get("break_even_orders_day", None)

    c1, c2, c3, c4 = st.columns(4)

    with c1:
        st.markdown(
            f"""
<div class="sp-metric">
  <div class="k">{t('rev_run')} <span class="sp-chip">{'Netto IVA' if st.session_state.get('lang','IT')=='IT' else 'Net of VAT'}</span></div>
  <div class="v">{_fmt_eur(rev_run)}</div>
  <div class="s">Benchmark: {label_business(bt_by_key[st.session_state['business_type_key']])}</div>
</div>
""",
            unsafe_allow_html=True,
        )

    with c2:
        st.markdown(
            f"""
<div class="sp-metric">
  <div class="k">{t('ebitda_run')}</div>
  <div class="v">{_fmt_eur(ebitda)}</div>
  <div class="s">Run-rate</div>
</div>
""",
            unsafe_allow_html=True,
        )

    with c3:
        st.markdown(
            f"""
<div class="sp-metric">
  <div class="k">{t('ebitda_pct')}</div>
  <div class="v">{_fmt_pct(ebitda_pct)}</div>
  <div class="s">Run-rate</div>
</div>
""",
            unsafe_allow_html=True,
        )

    with c4:
        st.markdown(
            f"""
<div class="sp-metric">
  <div class="k">{t('be_rev')}</div>
  <div class="v">{_fmt_eur(be_rev if isinstance(be_rev,(int,float)) else None)}</div>
  <div class="s">{t('be_orders')}: {_fmt_eur(be_orders if isinstance(be_orders,(int,float)) else None).replace(' €','')}</div>
</div>
""",
            unsafe_allow_html=True,
        )

    c5, c6, c7, c8 = st.columns(4)

    def _delta_chip(actual_pct: float, bench_pct: float) -> str:
        d_pp = (actual_pct - bench_pct) * 100.0
        sign = "+" if d_pp >= 0 else ""
        return f"{t('delta_vs')}: {sign}{d_pp:.1f} pp"

    with c5:
        st.markdown(
            f"""
<div class="sp-metric">
  <div class="k">COGS % <span class="sp-chip">{_delta_chip(actual_cogs_pct, bench_cogs)}</span></div>
  <div class="v">{_fmt_pct(actual_cogs_pct)}</div>
  <div class="s">{t('range')}: {int(c_mn*100)}–{int(c_mx*100)}%</div>
</div>
""",
            unsafe_allow_html=True,
        )

    with c6:
        st.markdown(
            f"""
<div class="sp-metric">
  <div class="k">Labor % <span class="sp-chip">{_delta_chip(actual_labor_pct, bench_labor)}</span></div>
  <div class="v">{_fmt_pct(actual_labor_pct)}</div>
  <div class="s">{t('range')}: {int(l_mn*100)}–{int(l_mx*100)}%</div>
</div>
""",
            unsafe_allow_html=True,
        )

    with c7:
        st.markdown(
            f"""
<div class="sp-metric">
  <div class="k">OPEX % <span class="sp-chip">{_delta_chip(actual_opex_pct, bench_opex)}</span></div>
  <div class="v">{_fmt_pct(actual_opex_pct)}</div>
  <div class="s">{t('range')}: {int(o_mn*100)}–{int(o_mx*100)}%</div>
</div>
""",
            unsafe_allow_html=True,
        )

    with c8:
        st.markdown(
            f"""
<div class="sp-metric">
  <div class="k">Marketing % <span class="sp-chip">{_delta_chip(actual_mkt_pct, bench_mkt)}</span></div>
  <div class="v">{_fmt_pct(actual_mkt_pct)}</div>
  <div class="s">{t('range')}: {int(m_mn*100)}–{int(m_mx*100)}%</div>
</div>
""",
            unsafe_allow_html=True,
        )

    if invest_enabled:
        st.markdown('<div class="sp-divider"></div>', unsafe_allow_html=True)
        st.markdown(f'<div class="sp-minihead">{icon("invest")} {t("invest_results")}</div>', unsafe_allow_html=True)
        i1, i2, i3, i4 = st.columns(4)

        with i1:
            st.markdown(
                f"""
<div class="sp-metric">
  <div class="k">{t('cash_invested')}</div>
  <div class="v">{_fmt_eur(cash_invested if cash_invested > 0 else None)}</div>
  <div class="s">{t('invest')}</div>
</div>
""",
                unsafe_allow_html=True,
            )
        with i2:
            st.markdown(
                f"""
<div class="sp-metric">
  <div class="k">{t('roi_run')}</div>
  <div class="v">{_fmt_pct(roi_run if isinstance(roi_run, (int, float)) else None)}</div>
  <div class="s">Run-rate</div>
</div>
""",
                unsafe_allow_html=True,
            )
        with i3:
            st.markdown(
                f"""
<div class="sp-metric">
  <div class="k">{t('payback_run')}</div>
  <div class="v">{_fmt_months(payback_run)}</div>
  <div class="s">Run-rate</div>
</div>
""",
                unsafe_allow_html=True,
            )
        with i4:
            if seasonality_enabled:
                st.markdown(
                    f"""
<div class="sp-metric">
  <div class="k">{t('payback_y1')}</div>
  <div class="v">{_fmt_months(payback_y1)}</div>
  <div class="s">{t('roi_y1')}: {_fmt_pct(roi_y1 if isinstance(roi_y1, (int, float)) else None)}</div>
</div>
""",
                    unsafe_allow_html=True,
                )
            else:
                st.markdown(
                    f"""
<div class="sp-metric">
  <div class="k">{t('roi_y1')}</div>
  <div class="v">{t('na')}</div>
  <div class="s">{t('seasonality')}</div>
</div>
""",
                    unsafe_allow_html=True,
                )

    if seasonality_enabled:
        st.markdown('<div class="sp-divider"></div>', unsafe_allow_html=True)
        st.markdown(f'<div class="sp-minihead">{icon("seasonality")} {t("y1_results")}</div>', unsafe_allow_html=True)
        y1c1, y1c2, y1c3, y1c4 = st.columns(4)

        ebitda_delta = y1_ebitda - ebitda
        delta_sign = "+" if ebitda_delta > 0 else ""
        y1_delta_txt = f"{delta_sign}{_fmt_eur(ebitda_delta).replace(' €', '')} €"

        with y1c1:
            st.markdown(
                f"""
<div class="sp-metric">
  <div class="k">{t('rev_y1')}</div>
  <div class="v">{_fmt_eur(y1_rev if y1_rev > 0 else None)}</div>
  <div class="s">{t('seasonality')}</div>
</div>
""",
                unsafe_allow_html=True,
            )
        with y1c2:
            st.markdown(
                f"""
<div class="sp-metric">
  <div class="k">{t('ebitda_y1')}</div>
  <div class="v">{_fmt_eur(y1_ebitda)}</div>
  <div class="s">Y1</div>
</div>
""",
                unsafe_allow_html=True,
            )
        with y1c3:
            st.markdown(
                f"""
<div class="sp-metric">
  <div class="k">{t('ebitda_pct_y1')}</div>
  <div class="v">{_fmt_pct(y1_ebitda_pct if isinstance(y1_ebitda_pct, (int, float)) else None)}</div>
  <div class="s">Y1</div>
</div>
""",
                unsafe_allow_html=True,
            )
        with y1c4:
            st.markdown(
                f"""
<div class="sp-metric">
  <div class="k">{t('delta_vs_run')}</div>
  <div class="v">{y1_delta_txt}</div>
  <div class="s">EBITDA</div>
</div>
""",
                unsafe_allow_html=True,
            )

    if fte_enabled:
        st.markdown('<div class="sp-divider"></div>', unsafe_allow_html=True)
        st.markdown(f'<div class="sp-minihead">{icon("fte")} {t("fte_results")}</div>', unsafe_allow_html=True)

        f1, f2, f3, f4 = st.columns(4)
        with f1:
            st.markdown(
                f"""
<div class="sp-metric">
  <div class="k">{t('labor_base')}</div>
  <div class="v">{_fmt_eur(fte_labor_base_annual if fte_labor_base_annual > 0 else None)}</div>
  <div class="s">{'Y1' if (fte_use_y1 and seasonality_enabled) else 'Run-rate'} - {t('fte_method')}</div>
</div>
""",
                unsafe_allow_html=True,
            )
        with f2:
            st.markdown(
                f"""
<div class="sp-metric">
  <div class="k">{t('labor_monthly_base')}</div>
  <div class="v">{_fmt_eur(fte_labor_base_month if fte_labor_base_month > 0 else None)}</div>
  <div class="s">{t('hourly_cost_used')}: {_fmt_eur(fte_hourly_cost).replace(' €','')} €/h</div>
</div>
""",
                unsafe_allow_html=True,
            )
        with f3:
            fte_hours_txt = f"{fte_labor_hours_annual:,.0f}".replace(",", ".")
            hours_per_fte_txt = f"{int(fte_hours_per_fte):,}".replace(",", ".")
            st.markdown(
                f"""
<div class="sp-metric">
  <div class="k">{t('labor_hours')}</div>
  <div class="v">{fte_hours_txt}</div>
  <div class="s">{t('hours_per_fte')}: {hours_per_fte_txt}</div>
</div>
""",
                unsafe_allow_html=True,
            )
        with f4:
            st.markdown(
                f"""
<div class="sp-metric">
  <div class="k">{t('fte_out')}</div>
  <div class="v">{fte_total:.2f}</div>
  <div class="s">{t('fte')}</div>
</div>
""",
                unsafe_allow_html=True,
            )

        if fte_daypart_rows:
            st.markdown(f"**{t('dp_staff_split')}**")
            h1, h2, h3, h4, h5, h6 = st.columns([2.2, 1.1, 1.4, 1.3, 0.9, 1.1])
            h1.markdown(f"**{t('dayparts')}**")
            h2.markdown(f"**{t('dp_staff_share')}**")
            h3.markdown(f"**{t('dp_staff_cost')}**")
            h4.markdown(f"**{t('dp_staff_hours')}**")
            h5.markdown(f"**{t('dp_staff_fte')}**")
            h6.markdown(f"**{t('dp_staff_heads')}**")

            for row in fte_daypart_rows:
                c1r, c2r, c3r, c4r, c5r, c6r = st.columns([2.2, 1.1, 1.4, 1.3, 0.9, 1.1])
                c1r.write(row["label"])
                c2r.write(_fmt_pct(row["share"]))
                c3r.write(_fmt_eur(row["cost_annual"]))
                c4r.write(f"{row['hours_annual']:,.0f}".replace(",", "."))
                c5r.write(f"{row['fte']:.2f}")
                c6r.write(f"{row['avg_on_shift']:.2f}" if isinstance(row["avg_on_shift"], (int, float)) else t("na"))

    # Assessment (visual scorecards)
    st.markdown('<div class="sp-divider"></div>', unsafe_allow_html=True)
    badge = "review"
    badge_label = "REVIEW"
    if isinstance(fe, dict):
        raw_status = str(fe.get("status", "REVIEW") or "REVIEW").upper()
        if raw_status == "NO_GO":
            badge_label = "NO GO"
        else:
            badge_label = raw_status
        # Normalize badge class
        if "GO" in badge_label and "NO" not in badge_label:
            badge = "go"
        elif "NO" in badge_label:
            badge = "nogo"
        else:
            badge = "review"

    st.markdown(
        f'<div class="sp-minihead emph">{icon("assessment")} {t("assessment")}<span class="sp-badge {badge}"><span class="sp-dot"></span>{badge_label}</span></div>',
        unsafe_allow_html=True,
    )

    def _score_high_better(v: float, low: float, high: float) -> int:
        if high <= low:
            return 0
        x = (v - low) / (high - low)
        return int(max(0, min(100, round(x * 100.0))))

    def _score_low_better(v: float, good: float, bad: float) -> int:
        if bad <= good:
            return 0
        x = 1.0 - ((v - good) / (bad - good))
        return int(max(0, min(100, round(x * 100.0))))

    ebitda_rr = _num(results.get("ebitda_pct_annual_runrate"), 0.0)
    prime_rr = _num(results.get("prime_cost_pct"), 0.0)
    occ_rr = _num(results.get("occupancy_pct"), 0.0)
    y1_pct_val = _num(results.get("ebitda_pct_annual_y1"), 0.0)

    assess_cards: List[Tuple[str, str, int]] = []
    assess_cards.append(("EBITDA %", _fmt_pct(ebitda_rr), _score_high_better(ebitda_rr, 0.08, 0.18)))
    assess_cards.append(("Prime Cost %", _fmt_pct(prime_rr), _score_low_better(prime_rr, 0.55, 0.70)))
    assess_cards.append(("Occupancy %", _fmt_pct(occ_rr), _score_low_better(occ_rr, 0.10, 0.18)))
    if seasonality_enabled:
        assess_cards.append(("EBITDA % Y1", _fmt_pct(y1_pct_val), _score_high_better(y1_pct_val, 0.06, 0.16)))
    if invest_enabled and cash_invested > 0:
        pay_val = _num(payback_run, 0.0)
        pay_txt = _fmt_months(payback_run)
        assess_cards.append(
            (
                "Payback" if st.session_state.get("lang", "IT") == "IT" else "Payback",
                pay_txt,
                _score_low_better(pay_val if pay_val > 0 else 999.0, 18.0, 48.0),
            )
        )

    for i in range(0, len(assess_cards), 3):
        cols = st.columns(3)
        chunk = assess_cards[i:i + 3]
        for idx, (lbl, val, score) in enumerate(chunk):
            with cols[idx]:
                st.markdown(
                    f"""
<div class="sp-metric">
  <div class="k">{lbl}</div>
  <div class="v">{val}</div>
  <div class="s">Score: {score}/100</div>
  <div class="sp-scorebar"><span style="width:{score}%;"></span></div>
</div>
""",
                    unsafe_allow_html=True,
                )

    if isinstance(fe, dict):
        msgs = fe.get("reasons") or fe.get("messages") or fe.get("notes") or []
        if msgs:
            for m in msgs:
                st.write(f"- {m}")
        else:
            st.write(fe)


# ============================
# Charts
# ============================
with st.container(border=True):
    st.markdown(f'<div class="sp-title">{icon("charts")} {t("charts")}</div>', unsafe_allow_html=True)

    # ----------------------------
    # Helpers
    # ----------------------------
    def _safe_float(x: Any, default: float = 0.0) -> float:
        try:
            if x is None:
                return float(default)
            return float(x)
        except Exception:
            return float(default)

    def _pick(d: Dict[str, Any], keys: List[str], default=None):
        for k in keys:
            if k in d and d.get(k) is not None:
                return d.get(k)
        return default

    def _fmt_signed_eur(v: float) -> str:
        sign = "+" if v > 0 else "−" if v < 0 else ""
        vv = abs(float(v))
        s = f"{vv:,.0f}".replace(",", "X").replace(".", ",").replace("X", ".")
        return f"{sign}{s} €"

    def _fmt_eur0(v: float) -> str:
        s = f"{float(v):,.0f}".replace(",", "X").replace(".", ",").replace("X", ".")
        return f"{s} €"

    lang_it = (st.session_state.get("lang", "IT") == "IT")

    def _chart_title_with_help(title: str, txt_it: str, txt_en: str) -> None:
        txt = (txt_it if lang_it else txt_en).strip()
        txt_html = _html_escape(txt).replace("\n", "<br>")
        st.markdown(
            f"""
            <div class="sp-chart-head">
              <div class="sp-chart-head-title">{_html_escape(title)}</div>
              <details class="sp-help-inline">
                <summary title="{_html_escape('Mostra spiegazione' if lang_it else 'Show explanation')}">?</summary>
                <div class="sp-help-panel">{txt_html}</div>
              </details>
            </div>
            """,
            unsafe_allow_html=True,
        )

    # ----------------------------
    # Revenue / EBITDA
    # ----------------------------
    rev_year = _safe_float(_pick(results, [
        "revenue_annual_runrate",
        "revenue_annual",
    ], 0.0))

    if rev_year <= 0:
        rev_month = _safe_float(_pick(results, [
            "revenue_monthly_runrate",
            "revenue_monthly",
        ], 0.0))
        if rev_month > 0:
            rev_year = rev_month * 12.0

    ebitda_year = _safe_float(_pick(results, [
        "ebitda_annual_runrate",
        "ebitda_annual",
    ], 0.0))

    be_rev_year = _safe_float(_pick(results, [
        "break_even_revenue_annual",
        "break_even_revenue_annual_runrate",
    ], 0.0), 0.0)

    # ----------------------------
    # Costs
    # ----------------------------
    cogs_year = _safe_float(results.get("cogs_annual_runrate", 0.0))
    labor_year = _safe_float(results.get("labor_annual_runrate", 0.0))
    opex_year = _safe_float(results.get("opex_annual_runrate", 0.0))
    mkt_year = _safe_float(results.get("marketing_annual_runrate", 0.0))
    fee_year = _safe_float(results.get("fee_annual_runrate", 0.0))
    occ_year = _safe_float(results.get("occupancy_annual_runrate", 0.0))

    total_costs_year = cogs_year + labor_year + opex_year + mkt_year + fee_year + occ_year

    # ----------------------------
    # Visual palette
    # ----------------------------
    _COL_REV = "rgba(132,102,91,0.92)"
    _COL_COST = "rgba(166,74,74,0.92)"
    _COL_EBITDA = "rgba(28,28,28,0.92)"
    _COL_SOFT = "rgba(184,149,129,0.85)"
    _COL_AREA = "rgba(132,102,91,0.12)"

    # =========================================================
    # Chart 1 — Waterfall Revenue → EBITDA
    # =========================================================
    if rev_year <= 0:
        fig_wf = _placeholder_figure(t("rev_margin"), kind="bar")
    else:
        labels = [
            "Ricavi" if lang_it else "Revenue",
            "COGS",
            "Labor" if not lang_it else "Personale",
            "OPEX",
            "Marketing",
            "Fee",
            "Occupancy",
            "EBITDA",
        ]

        values = [
            rev_year,
            -cogs_year,
            -labor_year,
            -opex_year,
            -mkt_year,
            -fee_year,
            -occ_year,
            ebitda_year,
        ]

        measures = ["absolute"] + ["relative"] * 6 + ["total"]

        fig_wf = go.Figure(
            go.Waterfall(
                x=labels,
                y=values,
                measure=measures,
                text=[_fmt_signed_eur(v) for v in values],
                textposition="outside",
                increasing=dict(marker=dict(color=_COL_REV)),
                decreasing=dict(marker=dict(color=_COL_COST)),
                totals=dict(marker=dict(color=_COL_EBITDA)),
                connector=dict(line=dict(color="rgba(16,24,40,0.25)", width=1.5)),
            )
        )

        fig_wf.update_yaxes(
            zeroline=True,
            zerolinewidth=2,
            zerolinecolor="rgba(16,24,40,0.25)",
            tickformat=",.0f",
        )

        fig_wf = _sp_base_layout(fig_wf)

    # =========================================================
    # Chart 2 — Break-even Curve
    # =========================================================
    def _fig_break_even_curve():

        if rev_year <= 0:
            return _placeholder_figure(t("be_curve"), kind="line")

        x_max = max(rev_year, be_rev_year or 0, 250000) * 1.3
        xs = [x_max * i / 60 for i in range(61)]

        var_rate = (
            (cogs_year + labor_year + opex_year + mkt_year + fee_year) / rev_year
            if rev_year > 0 else 0
        )

        fixed = occ_year

        total_costs = [fixed + var_rate * x for x in xs]
        ebitda_line = [x - c for x, c in zip(xs, total_costs)]

        fig = go.Figure()

        fig.add_trace(go.Scatter(x=xs, y=xs, mode="lines",
                                 name="Revenue" if not lang_it else "Ricavi",
                                 line=dict(color=_COL_REV, width=2)))

        fig.add_trace(go.Scatter(x=xs, y=total_costs, mode="lines",
                                 name="Costi totali" if lang_it else "Total costs",
                                 line=dict(color=_COL_SOFT, dash="dot", width=2)))

        fig.add_trace(go.Scatter(x=xs, y=ebitda_line, mode="lines",
                                 name="EBITDA",
                                 line=dict(color=_COL_EBITDA, width=2),
                                 fill="tozeroy",
                                 fillcolor=_COL_AREA))

        # Break-even
        be_x = None
        try:
            if (1 - var_rate) > 0:
                be_x = fixed / (1 - var_rate)
        except Exception:
            pass

        if be_x and be_x > 0:
            fig.add_vline(x=be_x, line_dash="dash",
                          line_width=3,
                          line_color="rgba(16,24,40,0.55)")

            fig.add_annotation(
                x=be_x,
                y=be_x,
                text=f"{'Punto di pareggio' if lang_it else 'Break-even'}: {_fmt_eur0(be_x)}",
                showarrow=True,
                arrowhead=3,
                arrowsize=1.3,
                arrowwidth=3,
                ax=50,
                ay=-50,
                bgcolor="rgba(255,255,255,0.92)",
                bordercolor="rgba(16,24,40,0.3)",
            )

        fig.update_xaxes(title="€", tickformat=",.0f")
        fig.update_yaxes(title="€", tickformat=",.0f")

        return _sp_base_layout(fig)

    fig_be = _fig_break_even_curve()

    # =========================================================
    # Chart 3 — Cost mix donut
    # =========================================================
    if total_costs_year <= 0:
        fig_pie = _placeholder_figure(t("cost_pie"), kind="pie")
    else:
        labels = ["COGS", "Labor", "OPEX", "Marketing", "Fee", "Occupancy"]
        values = [cogs_year, labor_year, opex_year, mkt_year, fee_year, occ_year]
        colors = [_COL_REV, _COL_SOFT, _COL_EBITDA,
                  "rgba(90,80,74,0.7)", "rgba(90,80,74,0.5)", _COL_COST]

        fig_pie = go.Figure(go.Pie(
            labels=labels,
            values=values,
            hole=0.62,
            marker=dict(colors=colors),
            textinfo="percent"
        ))

        fig_pie.update_layout(
            annotations=[dict(
                text=f"{'Costi' if lang_it else 'Costs'}<br><b>{_fmt_signed_eur(total_costs_year).replace('+','')}</b>",
                x=0.5, y=0.5,
                showarrow=False
            )]
        )

        fig_pie = _sp_base_layout(fig_pie)

    # =========================================================
    # Chart 4 — Daypart revenue
    # =========================================================
    open_days = int(st.session_state.get("open_days", 30) or 30)

    dp_labels = []
    dp_values = []

    for dp in daypart_inputs:
        try:
            val = (_safe_float(getattr(dp, "orders_per_day", 0)) *
                   _safe_float(getattr(dp, "ticket_avg", 0))) * open_days
            if val > 0:
                dp_labels.append(getattr(dp, "key", ""))
                dp_values.append(val)
        except:
            pass

    if not dp_values:
        fig_dp = _placeholder_figure(t("daypart_breakdown"), kind="bar")
    else:
        fig_dp = go.Figure(go.Bar(
            x=dp_labels,
            y=dp_values,
            marker=dict(color=_COL_REV),
            text=[_fmt_eur0(v) for v in dp_values],
            textposition="outside"
        ))

        fig_dp.update_yaxes(tickformat=",.0f")
        fig_dp = _sp_base_layout(fig_dp)

    # =========================================================
    # Render
    # =========================================================
    c1, c2 = st.columns(2)
    with c1:
        _chart_title_with_help(
            t("rev_margin"),
            "Ricavi -> EBITDA: parti dai ricavi annui; ogni barra negativa sottrae un costo. "
            "L'ultima barra mostra l'EBITDA finale.",
            "Revenue -> EBITDA: start from annual revenue; each negative bar subtracts a cost. "
            "The last bar shows final EBITDA.",
        )
        st.plotly_chart(fig_wf, use_container_width=True, key="chart_rev_margin")
    with c2:
        _chart_title_with_help(
            t("be_curve"),
            "Punto di pareggio: dove ricavi e costi totali si incontrano. "
            "Sotto quel livello EBITDA è negativo, sopra è positivo.",
            "Break-even point: where revenue and total costs intersect. "
            "Below that level EBITDA is negative, above it positive.",
        )
        st.plotly_chart(fig_be, use_container_width=True, key="chart_break_even")

    c3, c4 = st.columns(2)
    with c3:
        _chart_title_with_help(
            t("cost_pie"),
            "Mix costi annui: ogni fetta rappresenta una categoria di costo e la sua incidenza sul totale.",
            "Annual cost mix: each slice is a cost category and its share of total costs.",
        )
        st.plotly_chart(fig_pie, use_container_width=True, key="chart_cost_pie")
    with c4:
        _chart_title_with_help(
            t("daypart_breakdown"),
            "Ricavi per fascia: confronta il contributo mensile stimato di ciascuna fascia oraria.",
            "Revenue by daypart: compares estimated monthly contribution of each daypart.",
        )
        st.plotly_chart(fig_dp, use_container_width=True, key="chart_daypart_breakdown")

    # Store values
    st.session_state["_sp_rev_year"] = rev_year
    st.session_state["_sp_ebitda_year"] = ebitda_year
    st.session_state["_sp_total_costs_year"] = total_costs_year
    st.session_state["_sp_be_rev_year"] = be_rev_year
    
# ============================
# REPORT
# ============================

st.markdown('<div class="sp-section-divider"></div>', unsafe_allow_html=True)

with st.container(border=True):
    st.markdown(f'<div class="sp-title">{icon("report")} {t("report")}</div>', unsafe_allow_html=True)
    st.caption(t("report_caption"))

    # Report assets
    xlsx_bytes = _reports_payload.get("xlsx_bytes")
    pdf_bytes = _reports_payload.get("pdf_bytes")
    xlsx_err = _reports_payload.get("xlsx_err")
    pdf_err = _reports_payload.get("pdf_err")

    # Ready state for primary action (email delivery)
    ready = bool(xlsx_bytes) or bool(pdf_bytes)
    report_stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    lang = st.session_state.get("lang", "IT")

    st.markdown(f'<div class="sp-minihead">{icon("email")} {t("report_email_primary")}</div>', unsafe_allow_html=True)
    st.caption(t("report_email_caption"))

    lead_col1, lead_col2 = st.columns(2)
    with lead_col1:
        lead_email = st.text_input(t("lead_email"), key="lead_email_input")
    with lead_col2:
        lead_location = st.text_input(t("lead_location"), key="lead_location_input")

    privacy_url = str(_secret_get("privacy_policy_url", "") or "").strip()
    privacy_label = t("privacy_policy_cta")
    if privacy_url:
        st.markdown(f"[{privacy_label}]({privacy_url})")
    else:
        st.markdown(f"[{privacy_label}](?view=privacy)")

    privacy_ok = st.checkbox(t("lead_privacy"), key="lead_privacy_ok", value=False)
    marketing_ok = st.checkbox(t("lead_marketing"), key="lead_marketing_ok", value=False)

    if not ready:
        hint_it = "Per generare il report inserisci almeno 1 fascia con ordini e scontrino (ricavi > 0)."
        hint_en = "To generate the report, enter at least 1 daypart with orders and ticket (revenue > 0)."
        premium_info(hint_it if lang == "IT" else hint_en)

    send_c1, send_c2 = st.columns(2)
    with send_c1:
        send_pdf_clicked = st.button(
            t("send_pdf_email"),
            type="primary",
            use_container_width=True,
            disabled=(not bool(pdf_bytes)) or (not privacy_ok),
            key="send_pdf_email_btn",
        )
    with send_c2:
        send_xlsx_clicked = st.button(
            t("send_xlsx_email"),
            type="primary",
            use_container_width=True,
            disabled=(not bool(xlsx_bytes)) or (not privacy_ok),
            key="send_xlsx_email_btn",
        )

    if send_pdf_clicked or send_xlsx_clicked:
        errors: List[str] = []
        if not _is_valid_email(lead_email):
            errors.append(t("lead_missing_email"))
        if not str(lead_location or "").strip():
            errors.append(t("lead_missing_location"))
        if not privacy_ok:
            errors.append(t("lead_missing_privacy"))

        selected_fmt = "pdf" if send_pdf_clicked else "xlsx"
        selected_bytes = pdf_bytes if selected_fmt == "pdf" else xlsx_bytes
        selected_mime = "application/pdf" if selected_fmt == "pdf" else "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        selected_name = f"StorePilot_Report_{report_stamp}.{selected_fmt}"

        if not selected_bytes:
            errors.append(t("lead_missing_report"))

        if errors:
            for msg in errors:
                st.error(msg)
        else:
            try:
                lead_payload = build_lead_payload(
                    email=lead_email,
                    project_location=lead_location,
                    privacy_consent=privacy_ok,
                    marketing_consent=marketing_ok,
                    report_format=selected_fmt,
                    results=results,
                    lang=lang,
                )

                sheet_ok, _sheet_status = save_to_sheet(lead_payload)
                email_ok, _email_status = send_email_report(
                    to_email=str(lead_email).strip(),
                    report_bytes=bytes(selected_bytes),
                    report_filename=selected_name,
                    report_mime=selected_mime,
                    payload=lead_payload,
                )

                if sheet_ok and email_ok:
                    st.success(t("lead_ok_title"))
                    st.caption(
                        f"{t('lead_sent_email')}: {str(lead_email).strip().lower()} | "
                        f"{t('lead_sent_location')}: {str(lead_location).strip()} | "
                        f"{t('lead_sent_format')}: {selected_fmt.upper()}"
                    )
                    st.caption(t("lead_ok_msg"))
                    st.session_state["_sp_last_lead_payload"] = lead_payload
                else:
                    st.error(t("lead_send_error"))
            except Exception:
                st.error(t("lead_send_error"))

    st.markdown(f'<div class="sp-minihead">{icon("download")} {t("lead_local_downloads")}</div>', unsafe_allow_html=True)
    b1, b2 = st.columns(2)

    with b1:
        st.download_button(
            label=t("download_xlsx"),
            data=(xlsx_bytes if isinstance(xlsx_bytes, (bytes, bytearray)) else b""),
            file_name=f"StorePilot_Report_{report_stamp}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            type="secondary",
            use_container_width=True,
            disabled=not bool(xlsx_bytes),
        )
        if xlsx_err and not xlsx_bytes:
            st.caption(f"Excel: {xlsx_err}")

    with b2:
        st.download_button(
            label=t("download_pdf"),
            data=(pdf_bytes if isinstance(pdf_bytes, (bytes, bytearray)) else b""),
            file_name=f"StorePilot_Report_{report_stamp}.pdf",
            mime="application/pdf",
            type="secondary",
            use_container_width=True,
            disabled=not bool(pdf_bytes),
        )
        if pdf_err and not pdf_bytes:
            st.caption(f"PDF: {pdf_err}")

    st.caption(t("disclaimer"))
