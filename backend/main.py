"""HTTP API consumed by the frontend."""

from datetime import date
from typing import Literal

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from .recommender import recommend


app = FastAPI(title="Подбор подрядчиков", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    # Local demo frontend may run on a different port or be opened as a file.
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


class RecommendationRequest(BaseModel):
    city: Literal["Алматы", "Астана", "Зарубежье"]
    date: date
    event_type: str = Field(alias="event_format", min_length=1)
    category: str = Field(min_length=1)
    budget_kzt: int = Field(alias="budget", ge=0)
    duration_hours: float | None = Field(default=None, alias="duration", gt=0)
    language: Literal["русский", "казахский", "английский"] | None = None


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/api/recommend")
@app.post("/api/recommendations")
def recommendations(payload: RecommendationRequest) -> dict:
    result = recommend(payload.model_dump(mode="json"))
    status_map = {
        "ok": "success",
        "category_unavailable": "category_not_found",
        "no_match": "no_matches",
    }
    cards = []
    for item in result["recommendations"]:
        cards.append({
            "id": item["id"],
            "name": item["name"],
            "categories": item["categories"],
            "city": item["city"],
            "price": item["price_from_kzt"],
            "event_formats": item["event_formats"],
            "languages": item["languages"],
            "max_hours": item["max_hours"],
            "description": item["description"],
            "busy_days_count": item["busy_days_count"],
            "busy_window_days": item["busy_window_days"],
            "busy_december_count": item["busy_december_count"],
            "december_days": item["december_days"],
            "synthetic": item["synthetic"],
            "score": item["score"],
            "max_score": item["max_score"],
            "score_breakdown": item["score_breakdown"],
            "explanation": item["explanation"],
            "matches": item["matches"],
        })

    return {
        "status": status_map[result["status"]],
        "message": result["message"],
        "results": cards,
        "total_candidates": result["total_candidates"],
        "eligible_candidates": result.get("eligible_candidates", 0),
        "rejected": result.get("rejected", {}),
    }
