from __future__ import annotations
from typing import Dict, Any, List


def evaluate_feasibility(results: Dict[str, Any]) -> Dict[str, Any]:
    """
    Valuta la sostenibilità economica sulla base di:
    - EBITDA % annuo (run-rate)
    - Prime Cost %
    - Occupancy %

    Restituisce:
    {
        "status": "GO" | "REVIEW" | "NO_GO",
        "reasons": [...]
    }
    """

    reasons: List[str] = []

    ebitda_pct = float(results.get("ebitda_pct_annual_runrate", 0.0))
    prime_cost = float(results.get("prime_cost_pct", 0.0))
    occupancy = float(results.get("occupancy_pct", 0.0))

    # ----------------------------
    # EBITDA
    # ----------------------------
    if ebitda_pct < 0.08:
        reasons.append(
            "EBITDA annuo (run-rate) < 8%: struttura economica fortemente sotto pressione."
        )
    elif ebitda_pct < 0.12:
        reasons.append(
            "EBITDA annuo (run-rate) tra 8% e 12%: marginalità borderline, necessaria ottimizzazione."
        )
    elif ebitda_pct < 0.18:
        reasons.append(
            "EBITDA annuo (run-rate) tra 12% e 18%: livello sostenibile per molti format."
        )
    else:
        reasons.append(
            "EBITDA annuo (run-rate) ≥ 18%: marginalità robusta."
        )

    # ----------------------------
    # Prime Cost (COGS + Labor)
    # ----------------------------
    if prime_cost <= 0.55:
        reasons.append(
            "Prime Cost ≤ 55%: struttura operativa molto efficiente."
        )
    elif prime_cost <= 0.65:
        reasons.append(
            "Prime Cost tra 55% e 65%: livello gestibile ma da monitorare."
        )
    else:
        reasons.append(
            "Prime Cost > 65%: rischio su food cost o produttività del personale."
        )

    # ----------------------------
    # Occupancy
    # ----------------------------
    if occupancy <= 0.10:
        reasons.append(
            "Occupancy ≤ 10%: incidenza affitto molto sostenibile."
        )
    elif occupancy <= 0.14:
        reasons.append(
            "Occupancy tra 10% e 14%: livello accettabile."
        )
    else:
        reasons.append(
            "Occupancy > 14%: pressione elevata su affitto/oneri."
        )

    # ----------------------------
    # STATUS LOGIC
    # ----------------------------

    if (
        ebitda_pct >= 0.15
        and prime_cost <= 0.60
        and occupancy <= 0.12
    ):
        status = "GO"

    elif ebitda_pct >= 0.10:
        status = "REVIEW"

    else:
        status = "NO_GO"

    return {
        "status": status,
        "reasons": reasons,
    }