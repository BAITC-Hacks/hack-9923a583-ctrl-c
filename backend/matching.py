"""Hard eligibility filters and explainable match scoring."""

from datetime import date
import json
import math
import re


REJECTION_LABELS = {
    "busy": "заняты на выбранную дату",
    "budget": "цена выше бюджета",
    "format": "не работают с этим форматом мероприятия",
    "language": "не поддерживают выбранный язык",
    "duration": "не подходят по длительности",
}

# Points are intentionally simple enough to explain to a demo audience.
WEIGHTS = {
    "city": 10,
    "category": 10,
    "available_date": 15,
    "budget": 20,
    "event_format": 20,
    "language": 10,
    "duration": 15,
    "description_category": 5,
    "description_event": 5,
}

EVENT_TERMS = {
    "свадьба": ("свадеб", "свадьб"),
    "той": ("той",),
    "корпоратив": ("корпоратив",),
    "конференция": ("конференц",),
    "юбилей": ("юбилей",),
    "день рождения": ("день рождения", "дня рождения", "именин"),
}
CATEGORY_TERMS = {
    "флорист": ("флорист", "цветоч", "букет"),
    "фотограф": ("фотограф", "фотосъем", "фотосъём", "съемк", "съёмк"),
    "видеограф": ("видеограф", "видеосъем", "видеосъём", "видеосъемк", "видеосъёмк"),
    "ведущий": ("ведущ", "сценари", "веден"),
    "декоратор": ("декорат", "декор", "оформлен"),
    "банкетный зал": ("банкет", "банкетный зал", "площадк"),
    "ресторан": ("ресторан", "кухн", "меню"),
}


def parse_vendor(row) -> dict:
    """Convert a SQLite row into a plain object with list fields."""
    vendor = dict(row)
    for field in ("categories", "event_formats", "languages", "busy_dates"):
        vendor[field] = json.loads(vendor[field])
    return vendor


def rejection_reason(vendor: dict, request: dict) -> str | None:
    """Return the first hard-filter reason, or None when eligible."""
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


def _terms_for(category: str, mapping: dict[str, tuple[str, ...]]) -> tuple[str, ...]:
    normalized = category.casefold()
    for label, terms in mapping.items():
        if label in normalized:
            return terms
    words = re.findall(r"[а-яёa-z]+", normalized)
    return tuple(word for word in words if len(word) > 3)


def score_candidate(vendor: dict, request: dict) -> dict:
    """Score an already eligible profile and explain the evidence."""
    earned = {
        "city": WEIGHTS["city"],
        "category": WEIGHTS["category"],
        "available_date": WEIGHTS["available_date"],
        "budget": WEIGHTS["budget"] if vendor["price_from_kzt"] is not None else 0,
        "event_format": WEIGHTS["event_format"],
    }

    maximum = WEIGHTS["city"] + WEIGHTS["category"] + WEIGHTS["available_date"] + WEIGHTS["budget"] + WEIGHTS["event_format"]
    if request.get("language"):
        earned["language"] = WEIGHTS["language"]
        maximum += WEIGHTS["language"]

    duration = request.get("duration_hours")
    if duration is not None:
        maximum += WEIGHTS["duration"]
        capacity = vendor["max_hours"]
        if capacity is None:
            earned["duration"] = WEIGHTS["duration"]
        else:
            extra_hours = max(0, capacity - duration)
            earned["duration"] = max(0, WEIGHTS["duration"] - math.ceil(extra_hours * 3))

    description = vendor["description"].casefold()
    category_terms = _terms_for(request["category"], CATEGORY_TERMS)
    event_terms = _terms_for(request["event_type"], EVENT_TERMS)
    category_hits = [term for term in category_terms if term in description]
    event_hits = [term for term in event_terms if term in description]
    earned["description_category"] = WEIGHTS["description_category"] if category_hits else 0
    earned["description_event"] = WEIGHTS["description_event"] if event_hits else 0
    maximum += WEIGHTS["description_category"] + WEIGHTS["description_event"]

    score = round(sum(earned.values()) * 100 / maximum) if maximum else 0
    matches = ["Город", "Категория", "Свободен на дату", "В бюджете", "Формат"]
    if request.get("language"):
        matches.append(f"Язык: {request['language']}")
    if duration is not None:
        matches.append(f"Длительность: до {vendor['max_hours'] if vendor['max_hours'] is not None else 'без лимита'} ч.")
    if category_hits:
        matches.append(f"Описание: {category_hits[0]}")
    if event_hits:
        matches.append(f"Описание: {event_hits[0]}")

    price = vendor["price_from_kzt"]
    if price is None:
        price_reason = "Цена не указана в профиле; её нужно уточнить."
    else:
        price_reason = f"Цена от {price:,} ₸ укладывается в бюджет {request['budget_kzt']:,} ₸."
        price_reason = price_reason.replace(",", " ")
        if vendor["price_imputed"]:
            price_reason += " Цена оценочная."

    second_sentence = []
    if request.get("language"):
        second_sentence.append(f"Поддерживает язык «{request['language']}»")
    if duration is not None:
        capacity = vendor["max_hours"]
        second_sentence.append(
            "Не ограничен временем присутствия" if capacity is None
            else f"Лимит — {capacity:g} ч. при запросе {duration:g} ч."
        )
    if category_hits:
        second_sentence.append(f"в описании есть «{category_hits[0]}»")
    if event_hits:
        second_sentence.append(f"есть упоминание «{event_hits[0]}»")
    if not second_sentence:
        second_sentence.append(f"Свободен на выбранную дату; город — {vendor['city']}")
    if vendor["synthetic"]:
        second_sentence.append("это синтетический профиль")

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
        "score_breakdown": earned,
        "matches": matches,
        "explanation": (
            f"Берёт формат «{request['event_type']}»; {price_reason} "
            + "; ".join(second_sentence).capitalize() + "."
        ),
    }
