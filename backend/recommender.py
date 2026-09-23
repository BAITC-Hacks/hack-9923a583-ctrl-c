"""Search orchestration and result states for contractor recommendations."""

from collections import Counter
from datetime import date

from .db import connect
from .matching import REJECTION_LABELS, parse_vendor, rejection_reason, score_candidate


def _find_candidates(city: str, category: str) -> list[dict]:
    with connect() as connection:
        rows = connection.execute(
            "SELECT * FROM vendors WHERE city = ? ORDER BY id", (city,)
        ).fetchall()
    vendors = [parse_vendor(row) for row in rows]
    return [vendor for vendor in vendors if category in vendor["categories"]]


def recommend(request: dict) -> dict:
    """Filter, rank deterministically, and return up to three profiles."""
    request = dict(request)
    request["date"] = date.fromisoformat(request["date"]).isoformat()
    candidates = _find_candidates(request["city"], request["category"])

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
    for vendor in candidates:
        reason = rejection_reason(vendor, request)
        if reason:
            rejected[reason] += 1
        else:
            eligible.append(score_candidate(vendor, request))

    # Stable final tie-break makes identical requests return the same order.
    eligible.sort(key=lambda item: (-item["score"], item["id"]))
    selected = eligible[:3]
    if not selected:
        reasons = "; ".join(
            f"{count} {REJECTION_LABELS[key]}" for key, count in rejected.most_common()
        )
        message = f"Кандидаты есть ({len(candidates)}), но ни один не прошёл условия: {reasons}."
        status = "no_match"
    else:
        status = "ok"
        message = f"Подобрали {len(selected)} из {len(candidates)} кандидатов."
        if len(selected) < 3:
            reasons = "; ".join(
                f"{count} {REJECTION_LABELS[key]}" for key, count in rejected.most_common()
            )
            if reasons:
                message += f" Меньше трёх, потому что {reasons}."
            elif len(candidates) < 3:
                count = len(candidates)
                noun = "профиль" if count == 1 else "профиля"
                message += f" В каталоге этой категории всего {count} {noun}."

    return {
        "status": status,
        "message": message,
        "total_candidates": len(candidates),
        "eligible_candidates": len(eligible),
        "recommendations": selected,
        "rejected": dict(rejected),
        "rejection_labels": REJECTION_LABELS,
    }
