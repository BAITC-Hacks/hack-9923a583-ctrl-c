"""Hard eligibility filters and point-based ranking."""

import json
import re


MIN_RECOMMENDATION_RATIO = 0.25
PRICE_STEP_PERCENT = 3
MAX_BUDGET_OVERRUN_PERCENT = 30
REJECTION_LABELS = {
    "busy": "заняты на выбранную дату",
    "format": "не работают с выбранным форматом",
    "budget": "превышают бюджет более чем на 30%",
    "low_score": "не прошли порог 25% баллов",
}


def minimum_score(max_score: int) -> int:
    """Use a whole-point threshold; e.g. 25% of 27 is 6 points."""
    return int(max_score * MIN_RECOMMENDATION_RATIO)


def parse_vendor(row) -> dict:
    vendor = dict(row)
    for field in ("categories", "event_formats", "languages", "busy_dates"):
        vendor[field] = json.loads(vendor[field])
    return vendor


def rejection_reason(vendor: dict, request: dict) -> str | None:
    """Apply hard filters after city/category have selected the candidate pool."""
    if request["date"] in vendor["busy_dates"]:
        return "busy"
    if request["event_type"] not in vendor["event_formats"]:
        return "format"
    price = vendor["price_from_kzt"]
    budget = request["budget_kzt"]
    if price is not None and price * 100 > budget * (100 + MAX_BUDGET_OVERRUN_PERCENT):
        return "budget"
    return None


def score_candidate(vendor: dict, request: dict) -> dict:
    """Score soft matches. Lower-than-budget prices get no extra advantage."""
    breakdown = {
        "city": 1,
        "category": 1,
        "available_date": 1,
    }
    max_score = 3
    matches = ["Город", "Категория", "Свободен на дату"]

    # Prices at or below budget all receive the same 10 points. A permitted
    # overrun (up to 30%) loses one point for each started 3% band.
    price = vendor["price_from_kzt"]
    budget = request["budget_kzt"]
    max_score += 10
    if price is None:
        breakdown["budget"] = 0
    elif price <= budget:
        breakdown["budget"] = 10
        matches.append("В бюджете")
    else:
        overspend_steps = int(
            ((price - budget) * 100 + budget * PRICE_STEP_PERCENT - 1)
            // (budget * PRICE_STEP_PERCENT)
        )
        breakdown["budget"] = max(0, 10 - overspend_steps)

    requested_hours = request.get("duration_hours")
    if requested_hours is not None:
        max_score += 10
        capacity = vendor["max_hours"]
        if capacity is None or capacity >= requested_hours:
            breakdown["duration"] = 10
            matches.append("Длительность подходит")
        else:
            shortfall = requested_hours - capacity
            breakdown["duration"] = max(-10, round(10 - 2.5 * shortfall))

    max_score += 1
    breakdown["event_format"] = 1
    matches.append("Формат подходит")

    if request.get("language"):
        max_score += 3
        if request["language"] in vendor["languages"]:
            breakdown["language"] = 3
            matches.append(f"Язык: {request['language']}")
        else:
            breakdown["language"] = -1

    score = sum(breakdown.values())
    explanation = _explanation(vendor, request, breakdown)
    return {
        "id": vendor["id"],
        "name": vendor["anon_name"],
        "category": request["category"],
        "categories": vendor["categories"],
        "city": vendor["city"],
        "price_from_kzt": price,
        "event_formats": vendor["event_formats"],
        "languages": vendor["languages"],
        "max_hours": vendor["max_hours"],
        "description": vendor["description"],
        "busy_days_count": len(vendor["busy_dates"]),
        "busy_window_days": 100,
        "busy_december_count": sum(day.startswith("2026-12-") for day in vendor["busy_dates"]),
        "december_days": 31,
        "synthetic": bool(vendor["synthetic"]),
        "score": score,
        "max_score": max_score,
        "score_breakdown": breakdown,
        "matches": matches,
        "explanation": explanation,
    }


def _explanation(vendor: dict, request: dict, score: dict) -> str:
    price = vendor["price_from_kzt"]
    budget = request["budget_kzt"]
    if price is None:
        price_text = "Цена не указана — её нужно уточнить (0 баллов за бюджет)"
    elif price <= budget:
        price_text = f"Цена от {price:,} ₸ укладывается в бюджет {budget:,} ₸ (+10 баллов)".replace(",", " ")
    else:
        over = price - budget
        price_text = (
            f"Цена от {price:,} ₸ выше бюджета на {over:,} ₸ "
            f"({score['budget']:+d} баллов за бюджет)"
        ).replace(",", " ")

    format_text = f"берёт формат «{request['event_type']}» ({score['event_format']:+d})"
    first_sentence = f"{price_text}; {format_text}."

    notes = []
    if request.get("language"):
        if request["language"] in vendor["languages"]:
            notes.append(f"поддерживает язык «{request['language']}» (+3)")
        else:
            notes.append(f"не указал язык «{request['language']}» (−1)")
    hours = request.get("duration_hours")
    if hours is not None:
        capacity = vendor["max_hours"]
        if capacity is None:
            notes.append("работа не привязана ко времени присутствия (+10 за длительность)")
        elif capacity >= hours:
            notes.append(f"лимит {capacity:g} ч. покрывает запрос {hours:g} ч. (+10 за длительность)")
        else:
            notes.append(
                f"лимит {capacity:g} ч. меньше запрошенных {hours:g} ч. "
                f"({score['duration']:+d} за длительность)"
            )
    if vendor["synthetic"]:
        notes.append("синтетический демо-профиль")
    if vendor["price_imputed"]:
        notes.append("цена оценочная")
    notes.append(f"свободен на дату {request['date']}")
    description = " ".join(vendor["description"].split())
    if description:
        # Quote an actual profile fact, retaining spelling and proper names.
        # Prefer a complete sentence; truncate long ones at a word boundary.
        excerpt = re.split(r"(?<=[.!?])\s+", description, maxsplit=1)[0]
        if len(excerpt) > 180:
            excerpt = excerpt[:180].rsplit(" ", 1)[0].rstrip(",;:") + "…"
        excerpt = excerpt.rstrip(".!?")
        notes.append(f"в описании: «{excerpt}»")
    details = "; ".join(notes)
    return first_sentence + " " + details[0].upper() + details[1:] + "."
