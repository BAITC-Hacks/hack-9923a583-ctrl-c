"""Eligibility filters, transparent scoring, and explanations."""

from collections import Counter
from datetime import date
import json
import math
import re

try:
    from .db import connect
except ImportError:  # pragma: no cover - direct script imports
    from db import connect


REJECTION_LABELS = {
    "busy": "заняты на выбранную дату",
    "budget": "цена выше бюджета",
    "format": "не работают с этим форматом мероприятия",
    "language": "не поддерживают выбранный язык",
    "duration": "не подходят по длительности",
}
DURATION_STEP_POINTS = 5
DESCRIPTION_STEP_POINTS = 4
DESCRIPTION_MAX_POINTS = 12
STOP_WORDS = {"для", "или", "это", "как", "при", "под", "над", "the", "and"}


def get_city_category_vendors(city: str, category: str) -> list[dict]:
    with connect() as connection:
        rows = connection.execute(
            "SELECT * FROM vendors WHERE city = ? ORDER BY id", (city,)
        ).fetchall()
    return [
        dict(row) for row in rows
        if category in json.loads(row["categories"])
    ]


def _lists(vendor: dict) -> dict:
    result = dict(vendor)
    for field in ("categories", "event_formats", "languages", "busy_dates"):
        result[field] = json.loads(result[field])
    return result


def _rejection(vendor: dict, request: dict) -> str | None:
    if request["date"] in vendor["busy_dates"]:
        return "busy"
    price = vendor["price_from_kzt"]
    if price is not None and price > request["budget_kzt"]:
        return "budget"
    if request["event_type"] not in vendor["event_formats"]:
        return "format"
    if request.get("language") and request["language"] not in vendor["languages"]:
        return "language"
    hours = request.get("duration_hours")
    if hours is not None and vendor["max_hours"] is not None and hours > vendor["max_hours"]:
        return "duration"
    return None


def _score_and_explain(vendor: dict, request: dict) -> dict:
    score = 0
    reasons = []

    # Hard-filtered matches still contribute to a readable, comparable score.
    score += 35
    reasons.append(f"Подходит для формата «{request['event_type']}».")

    if request.get("language"):
        score += 20
        reasons.append(f"Работает на языке: {request['language']}.")

    requested_hours = request.get("duration_hours")
    if requested_hours is not None:
        max_hours = vendor["max_hours"]
        if max_hours is None:
            score += 10
            reasons.append("Работа не ограничена временем присутствия.")
        else:
            # Prefer a closer declared capacity; every extra hour costs 5 points.
            gap = max(0, max_hours - requested_hours)
            duration_score = max(0, 15 - math.ceil(gap * DURATION_STEP_POINTS))
            score += duration_score
            reasons.append(
                f"Лимит {max_hours:g} ч. подходит под запрос {requested_hours:g} ч. "
                f"(совпадение по длительности: {duration_score} баллов)."
            )

    price = vendor["price_from_kzt"]
    if price is not None:
        score += 20
        price_text = f"Цена от {price:,} ₸ укладывается в бюджет {request['budget_kzt']:,} ₸.".replace(",", " ")
        if vendor["price_imputed"]:
            price_text += " Цена оценочная."
        reasons.append(price_text)
    else:
        reasons.append("Цена в профиле не указана — её стоит уточнить.")

    if vendor["city_imputed"]:
        reasons.append("Город в профиле был восстановлен при подготовке данных.")
    if vendor["synthetic"]:
        reasons.append("Демо-профиль создан синтетически.")

    # Small, explainable lexical bonus for concrete terms in the profile text.
    query_terms = {
        word.lower() for word in re.findall(r"[а-яёa-z0-9]+", f"{request['category']} {request['event_type']}", re.I)
        if len(word) > 3 and word.lower() not in STOP_WORDS
    }
    description = vendor["description"].lower()
    matched_terms = sorted(term for term in query_terms if term in description)
    if matched_terms:
        semantic_score = min(DESCRIPTION_MAX_POINTS, len(matched_terms) * DESCRIPTION_STEP_POINTS)
        score += semantic_score
        reasons.append(
            f"В описании есть слова по запросу: {', '.join(matched_terms)} "
            f"(+{semantic_score} баллов)."
        )

    price_summary = ""
    if price is not None:
        price_summary = f"Цена от {price:,} ₸ в пределах бюджета {request['budget_kzt']:,} ₸.".replace(",", " ")
        if vendor["price_imputed"]:
            price_summary += " Цена оценочная."
    else:
        price_summary = "Цена в профиле не указана, её нужно уточнить."
    explanation = f"Берёт формат «{request['event_type']}»; {price_summary}"

    differentiators = []
    if request.get("language"):
        differentiators.append(f"язык — {request['language']}")
    if requested_hours is not None:
        if vendor["max_hours"] is None:
            differentiators.append("работа не ограничена временем присутствия")
        else:
            differentiators.append(
                f"лимит времени — {vendor['max_hours']:g} ч. при запросе {requested_hours:g} ч."
            )
    if matched_terms:
        differentiators.append(f"в описании упоминается: {', '.join(matched_terms)}")
    if vendor["synthetic"]:
        differentiators.append("это синтетический демо-профиль")
    if vendor["city_imputed"]:
        differentiators.append("город в профиле восстановлен при подготовке данных")
    if not differentiators:
        differentiators.append(f"профиль относится к категории «{request['category']}» в городе {vendor['city']}")
    explanation += " " + differentiators[0].capitalize() + "."

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
        "synthetic": bool(vendor["synthetic"]),
        "score": score,
        "explanation": explanation,
        "details": reasons,
    }


def recommend(request: dict) -> dict:
    # Input date is ISO YYYY-MM-DD; normalizing here makes date comparisons exact.
    request = dict(request)
    request["date"] = date.fromisoformat(request["date"]).isoformat()
    candidates = [_lists(v) for v in get_city_category_vendors(request["city"], request["category"])]

    if not candidates:
        return {
            "status": "category_unavailable",
            "message": f"В городе {request['city']} нет подрядчиков категории «{request['category']}».",
            "total_candidates": 0,
            "recommendations": [],
            "rejected": {},
        }

    rejected = Counter()
    eligible = []
    for vendor in candidates:
        reason = _rejection(vendor, request)
        if reason:
            rejected[reason] += 1
        else:
            eligible.append(_score_and_explain(vendor, request))

    eligible.sort(key=lambda item: (-item["score"], item["id"]))
    selected = eligible[:3]
    if not selected:
        why = "; ".join(
            f"{count} {REJECTION_LABELS[key]}" for key, count in rejected.most_common()
        )
        message = f"Кандидаты есть ({len(candidates)}), но ни один не прошёл условия: {why}."
        status = "no_match"
    else:
        status = "ok"
        message = f"Подобрали {len(selected)} из {len(candidates)} кандидатов."
        if len(selected) < 3:
            why = "; ".join(
                f"{count} {REJECTION_LABELS[key]}" for key, count in rejected.most_common()
            )
            message += f" Меньше трёх, потому что {why}."

    return {
        "status": status,
        "message": message,
        "total_candidates": len(candidates),
        "eligible_candidates": len(eligible),
        "recommendations": selected,
        "rejected": dict(rejected),
        "rejection_labels": REJECTION_LABELS,
    }
