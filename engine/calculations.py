from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, List, Dict, Any, Tuple


@dataclass
class DaypartInput:
    key: str
    label: str
    orders_per_day: float
    ticket_avg: float
    start_time: Optional[str] = None  # "HH:MM"
    end_time: Optional[str] = None    # "HH:MM"


def _parse_hhmm(s: str) -> Optional[Tuple[int, int]]:
    if not s:
        return None
    s = s.strip()
    if len(s) != 5 or s[2] != ":":
        return None
    try:
        hh = int(s[:2])
        mm = int(s[3:])
        if 0 <= hh <= 23 and 0 <= mm <= 59:
            return hh, mm
    except Exception:
        return None
    return None


def _hours_between(start: Optional[str], end: Optional[str]) -> Optional[float]:
    ps = _parse_hhmm(start) if start else None
    pe = _parse_hhmm(end) if end else None
    if not ps or not pe:
        return None
    sh, sm = ps
    eh, em = pe
    smin = sh * 60 + sm
    emin = eh * 60 + em
    if emin == smin:
        return None
    if emin < smin:
        emin += 24 * 60
    return (emin - smin) / 60.0


def _cost_amount(revenue_month: float, mode: str, pct: float, eur_month: float) -> float:
    mode = (mode or "pct").lower()
    if mode == "eur":
        return float(eur_month)
    return float(revenue_month) * float(pct)


def calculate_financials(
    *,
    dayparts: List[DaypartInput],
    open_days_per_month: int,

    # cost modes
    cogs_mode: str,
    labor_mode: str,
    opex_mode: str,
    marketing_mode: str,
    fee_mode: str,

    # cost values
    cogs_pct: float,
    labor_pct: float,
    opex_pct: float,
    marketing_pct: float,
    fee_pct: float,

    cogs_eur: float,
    labor_eur: float,
    opex_eur: float,
    marketing_eur: float,
    fee_eur: float,

    # fixed occupancy
    rent_fixed_month: float,
    service_charges_month: float,

    # investments (optional)
    capex: float,
    deposits: float,
    immobilizations: float,
    guarantees: float,

    # optional seasonality + ramp-up (Y1 view)
    quarter_weights: Optional[List[float]] = None,  # 4 values
    ramp_up_months: int = 0,
    ramp_up_floor: float = 0.65,
) -> Dict[str, Any]:
    open_days_per_month = int(open_days_per_month)

    # ---- Revenue
    orders_day = sum(max(0.0, float(dp.orders_per_day)) for dp in dayparts)
    revenue_day = sum(
        max(0.0, float(dp.orders_per_day)) * max(0.0, float(dp.ticket_avg))
        for dp in dayparts
    )
    revenue_month = revenue_day * open_days_per_month
    revenue_annual_runrate = revenue_month * 12

    # ---- Hourly analytics
    total_hours = 0.0
    hours_by_daypart: Dict[str, float] = {}
    for dp in dayparts:
        h = _hours_between(dp.start_time, dp.end_time) if (dp.start_time and dp.end_time) else None
        if h is not None and h > 0:
            hours_by_daypart[dp.key] = h
            total_hours += h

    hours_day_total = total_hours if total_hours > 0 else None
    orders_per_hour = (orders_day / hours_day_total) if hours_day_total else None
    revenue_per_hour = (revenue_day / hours_day_total) if hours_day_total else None

    # ---- Costs (monthly)
    cogs_month = _cost_amount(revenue_month, cogs_mode, cogs_pct, cogs_eur)
    labor_month = _cost_amount(revenue_month, labor_mode, labor_pct, labor_eur)
    opex_month = _cost_amount(revenue_month, opex_mode, opex_pct, opex_eur)
    marketing_month = _cost_amount(revenue_month, marketing_mode, marketing_pct, marketing_eur)
    fee_month = _cost_amount(revenue_month, fee_mode, fee_pct, fee_eur)

    occupancy_month = float(rent_fixed_month) + float(service_charges_month)

    ebitda_month = revenue_month - (cogs_month + labor_month + opex_month + marketing_month + fee_month + occupancy_month)
    ebitda_pct_month = (ebitda_month / revenue_month) if revenue_month > 0 else 0.0

    # ---- Annual run-rate
    cogs_annual = cogs_month * 12
    labor_annual = labor_month * 12
    opex_annual = opex_month * 12
    marketing_annual = marketing_month * 12
    fee_annual = fee_month * 12
    occupancy_annual = occupancy_month * 12

    ebitda_annual = ebitda_month * 12
    ebitda_pct_annual = (ebitda_annual / revenue_annual_runrate) if revenue_annual_runrate > 0 else 0.0

    # ---- KPI buckets
    prime_cost_pct = ((cogs_annual + labor_annual) / revenue_annual_runrate) if revenue_annual_runrate > 0 else 0.0
    occupancy_pct = (occupancy_annual / revenue_annual_runrate) if revenue_annual_runrate > 0 else 0.0
    controllable_costs_pct = (
        (cogs_annual + labor_annual + opex_annual + marketing_annual + fee_annual) / revenue_annual_runrate
    ) if revenue_annual_runrate > 0 else 0.0

    # ---- Investments / ROI
    cash_invested = float(capex) + float(deposits) + float(immobilizations) + float(guarantees)
    roi_annual = (ebitda_annual / cash_invested) if cash_invested > 0 else None
    payback_months = (cash_invested / ebitda_month) if (cash_invested > 0 and ebitda_month > 0) else None

    # ---- Break-even (FIX)
    variable_rate = 0.0
    fixed_month_extra = 0.0

    for mode, pct, eur in [
        (cogs_mode, cogs_pct, cogs_eur),
        (labor_mode, labor_pct, labor_eur),
        (opex_mode, opex_pct, opex_eur),
        (marketing_mode, marketing_pct, marketing_eur),
        (fee_mode, fee_pct, fee_eur),
    ]:
        if (mode or "pct").lower() == "pct":
            variable_rate += float(pct)
        else:
            fixed_month_extra += float(eur)

    fixed_costs_month = occupancy_month + fixed_month_extra
    contribution_margin = 1.0 - variable_rate

    break_even_revenue_month = None
    break_even_revenue_annual = None

    # Mostra BE solo se esistono costi fissi > 0 e contribution margin > 0
    if contribution_margin > 0 and fixed_costs_month > 0:
        break_even_revenue_month = fixed_costs_month / contribution_margin
        break_even_revenue_annual = break_even_revenue_month * 12

    blended_ticket = (revenue_day / orders_day) if orders_day > 0 else None
    break_even_orders_day = None
    if (
        break_even_revenue_month is not None
        and blended_ticket
        and blended_ticket > 0
        and open_days_per_month > 0
    ):
        break_even_orders_day = break_even_revenue_month / (blended_ticket * open_days_per_month)

    # Break-even curve
    be_curve = None
    if contribution_margin > 0 and fixed_costs_month > 0:
        fixed_costs_annual = fixed_costs_month * 12
        x_max = max(revenue_annual_runrate * 1.4, (break_even_revenue_annual or 0.0) * 1.6, 150_000.0)
        xs = [i * (x_max / 60.0) for i in range(0, 61)]
        total_costs = [fixed_costs_annual + (1.0 - contribution_margin) * x for x in xs]
        ebitdas = [x - tc for x, tc in zip(xs, total_costs)]
        be_curve = {
            "revenue_annual": xs,
            "total_costs_annual": total_costs,
            "ebitda_annual": ebitdas,
            "break_even_revenue_annual": break_even_revenue_annual,
            "fixed_costs_annual": fixed_costs_annual,
            "variable_rate": (1.0 - contribution_margin),
            "contribution_margin": contribution_margin,
        }

    # ---- Y1 view (seasonality + ramp-up)
    ramp_up_months = int(max(0, min(12, ramp_up_months)))
    ramp_up_floor = float(max(0.0, min(1.0, ramp_up_floor)))

    if quarter_weights and len(quarter_weights) == 4 and sum(quarter_weights) > 0:
        qw = [float(x) for x in quarter_weights]
        s = sum(qw)
        qw = [q / s for q in qw]
        month_weights = []
        for q in qw:
            month_weights.extend([q / 3.0] * 3)
    else:
        month_weights = [1.0 / 12.0] * 12

    factors = [1.0] * 12
    if ramp_up_months > 0:
        for m in range(ramp_up_months):
            if ramp_up_months == 1:
                factors[m] = 1.0
            else:
                factors[m] = ramp_up_floor + (1.0 - ramp_up_floor) * (m / (ramp_up_months - 1))

    mw_no_renorm = [month_weights[i] * factors[i] for i in range(12)]
    revenue_annual_y1 = revenue_annual_runrate * sum(mw_no_renorm)

    def _annual_cost_y1(mode: str, pct: float, eur_month: float, y1_revenue: float) -> float:
        if (mode or "pct").lower() == "pct":
            return float(max(0.0, pct) * max(0.0, y1_revenue))
        return float(max(0.0, eur_month) * 12.0)

    cogs_annual_y1 = _annual_cost_y1(cogs_mode, cogs_pct, cogs_eur, revenue_annual_y1)
    labor_annual_y1 = _annual_cost_y1(labor_mode, labor_pct, labor_eur, revenue_annual_y1)
    opex_annual_y1 = _annual_cost_y1(opex_mode, opex_pct, opex_eur, revenue_annual_y1)
    marketing_annual_y1 = _annual_cost_y1(marketing_mode, marketing_pct, marketing_eur, revenue_annual_y1)
    fee_annual_y1 = _annual_cost_y1(fee_mode, fee_pct, fee_eur, revenue_annual_y1)
    occupancy_annual_y1 = occupancy_month * 12.0

    total_costs_annual_y1 = (
        cogs_annual_y1
        + labor_annual_y1
        + opex_annual_y1
        + marketing_annual_y1
        + fee_annual_y1
        + occupancy_annual_y1
    )
    ebitda_annual_y1 = revenue_annual_y1 - total_costs_annual_y1
    ebitda_pct_annual_y1 = (ebitda_annual_y1 / revenue_annual_y1) if revenue_annual_y1 > 0 else 0.0

    roi_annual_y1 = (ebitda_annual_y1 / cash_invested) if cash_invested > 0 else None
    payback_months_y1 = (cash_invested / (ebitda_annual_y1 / 12.0)) if (cash_invested > 0 and ebitda_annual_y1 > 0) else None

    return {
        "orders_day": orders_day,
        "revenue_day": revenue_day,
        "revenue_month": revenue_month,
        "revenue_annual_runrate": revenue_annual_runrate,

        "cogs_month": cogs_month,
        "labor_month": labor_month,
        "opex_month": opex_month,
        "marketing_month": marketing_month,
        "fee_month": fee_month,
        "occupancy_month": occupancy_month,

        "cogs_annual_runrate": cogs_annual,
        "labor_annual_runrate": labor_annual,
        "opex_annual_runrate": opex_annual,
        "marketing_annual_runrate": marketing_annual,
        "fee_annual_runrate": fee_annual,
        "occupancy_annual_runrate": occupancy_annual,

        "ebitda_month": ebitda_month,
        "ebitda_pct_month": ebitda_pct_month,
        "ebitda_annual_runrate": ebitda_annual,
        "ebitda_pct_annual_runrate": ebitda_pct_annual,

        "prime_cost_pct": prime_cost_pct,
        "occupancy_pct": occupancy_pct,
        "controllable_costs_pct": controllable_costs_pct,

        "cash_invested": cash_invested,
        "capex": float(capex),
        "deposits": float(deposits),
        "immobilizations": float(immobilizations),
        "guarantees": float(guarantees),
        "payback_months": payback_months,
        "roi_annual": roi_annual,

        "break_even_revenue_month": break_even_revenue_month,
        "break_even_revenue_annual": break_even_revenue_annual,
        "break_even_orders_day": break_even_orders_day,
        "break_even_curve": be_curve,
        "fixed_costs_month": fixed_costs_month,
        "variable_rate": (1.0 - contribution_margin) if contribution_margin is not None else None,
        "contribution_margin": contribution_margin,

        "quarter_weights": quarter_weights,
        "ramp_up_months": ramp_up_months,
        "ramp_up_floor": ramp_up_floor,
        "revenue_annual_y1": revenue_annual_y1,
        "cogs_annual_y1": cogs_annual_y1,
        "labor_annual_y1": labor_annual_y1,
        "opex_annual_y1": opex_annual_y1,
        "marketing_annual_y1": marketing_annual_y1,
        "fee_annual_y1": fee_annual_y1,
        "occupancy_annual_y1": occupancy_annual_y1,
        "ebitda_annual_y1": ebitda_annual_y1,
        "ebitda_pct_annual_y1": ebitda_pct_annual_y1,
        "roi_annual_y1": roi_annual_y1,
        "payback_months_y1": payback_months_y1,

        "hours_day_total": hours_day_total,
        "orders_per_hour": orders_per_hour,
        "revenue_per_hour": revenue_per_hour,
        "hours_by_daypart": hours_by_daypart,
    }
