"""Search orchestration and result states for contractor recommendations."""

from collections import Counter
from datetime import date
from pathlib import Path

from .db import DEFAULT_DB_PATH, catalog_connection
from .matching import (
    REJECTION_LABELS,
    minimum_score,
    parse_vendor,
    rejection_reason,
    score_candidate,
)


def _rejection_summary(rejected: Counter) -> str:
    phrases = []
    for reason, count in rejected.most_common():
        last_two = count % 100
        last_one = count % 10
        few = last_two not in range(11, 15) and last_one in (2, 3, 4)
        one = last_two != 11 and last_one == 1
        if reason == "busy":
            phrase = "профиль занят" if one else "профиля заняты" if few else "профилей заняты"
            phrases.append(f"{count} {phrase} на выбранную дату")
        elif reason == "format":
            phrase = "подрядчик не берёт" if one else "подрядчика не берут" if few else "подрядчиков не берут"
            phrases.append(f"{count} {phrase} выбранный формат")
        elif reason == "budget":
            phrase = "подрядчик превышает" if one else "подрядчика превышают" if few else "подрядчиков превышают"
            phrases.append(f"{count} {phrase} бюджет более чем на 30%")
        else:
            phrase = "кандидат не прошёл" if one else "кандидата не прошли" if few else "кандидатов не прошли"
            phrases.append(f"{count} {phrase} порог 25% баллов")
    return "; ".join(phrases)


def _find_candidates(city: str, category: str, db_path: Path | str) -> list[dict]:
    with catalog_connection(db_path) as connection:
        rows = connection.execute(
            "SELECT * FROM vendors WHERE city = ? ORDER BY id", (city,)
        ).fetchall()
    vendors = [parse_vendor(row) for row in rows]
    return [vendor for vendor in vendors if category in vendor["categories"]]


def recommend(request: dict, db_path: Path | str = DEFAULT_DB_PATH) -> dict:
    """Filter, rank deterministically, and return up to three profiles."""
    request = dict(request)
    request["date"] = date.fromisoformat(request["date"]).isoformat()
    candidates = _find_candidates(request["city"], request["category"], db_path)

    if not candidates:
        return {
            "status": "category_unavailable",
            "message": f"В городе {request['city']} нет подрядчиков категории «{request['category']}».",
            "total_candidates": 0,
            "recommendations": [],
            "eligible_candidates": 0,
            "rejected": {},
        }

    rejected = Counter()
    eligible = []
    threshold = None
    for vendor in candidates:
        reason = rejection_reason(vendor, request)
        if reason:
            rejected[reason] += 1
        else:
            item = score_candidate(vendor, request)
            threshold = minimum_score(item["max_score"])
            if item["score"] < threshold:
                rejected["low_score"] += 1
            else:
                eligible.append(item)

    # Stable final tie-break makes identical requests return the same order.
    eligible.sort(key=lambda item: (-item["score"], item["id"]))
    selected = eligible[:3]
    if not selected:
        reasons = _rejection_summary(rejected)
        message = f"Кандидаты есть ({len(candidates)}), но ни один не прошёл условия: {reasons}."
        status = "no_match"
    else:
        status = "ok"
        message = f"Подобрали {len(selected)} из {len(candidates)} кандидатов."
        if len(selected) < 3:
            reasons = _rejection_summary(rejected)
            if reasons:
                message += f" Меньше трёх, потому что {reasons}."
            elif len(candidates) < 3:
                count = len(candidates)
                noun = "профиль" if count == 1 else "профиля"
                message += f" В каталоге этой категории всего {count} {noun}."
        elif rejected:
            message += f" Не подошли: {_rejection_summary(rejected)}."

    return {
        "status": status,
        "message": message,
        "total_candidates": len(candidates),
        "eligible_candidates": len(eligible),
        "minimum_score": threshold,
        "recommendations": selected,
        "rejected": dict(rejected),
        "rejection_labels": REJECTION_LABELS,
    }
