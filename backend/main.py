"""HTTP API consumed by the frontend."""

from contextlib import asynccontextmanager
from datetime import date
import logging
from pathlib import Path
from typing import Literal

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator

from .db import DEFAULT_DB_PATH, CatalogNotReadyError, catalog_size
from .recommender import recommend


DATE_MIN = date(2026, 9, 23)
DATE_MAX = date(2026, 12, 31)
logger = logging.getLogger(__name__)


class RecommendationRequest(BaseModel):
    city: Literal["Алматы", "Астана", "Зарубежье"]
    date: date
    event_type: Literal[
        "свадьба", "той", "корпоратив", "конференция", "юбилей", "день рождения"
    ] = Field(alias="event_format")
    category: str = Field(min_length=1)
    budget_kzt: int = Field(alias="budget", gt=0)
    duration_hours: float | None = Field(
        default=None, alias="duration", gt=0, allow_inf_nan=False
    )
    language: Literal["русский", "казахский", "английский"] | None = None

    @field_validator("category", mode="before")
    @classmethod
    def strip_category(cls, value):
        return value.strip() if isinstance(value, str) else value

    @field_validator("date")
    @classmethod
    def date_must_be_in_catalog_window(cls, value: date) -> date:
        if not DATE_MIN <= value <= DATE_MAX:
            raise ValueError("Дата должна быть с 23.09.2026 по 31.12.2026 включительно.")
        return value


def _recommendation_response(result: dict) -> dict:
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
        "minimum_score": result.get("minimum_score"),
        "rejected": result.get("rejected", {}),
    }


def create_app(db_path: Path | str = DEFAULT_DB_PATH) -> FastAPI:
    """Create the API; a separate catalog path also allows isolated tests."""
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        try:
            catalog_size(db_path)
        except CatalogNotReadyError as exc:
            # Keep health available to explain the problem. Requests recheck
            # readiness so importing the catalog does not require a restart.
            logger.warning("%s", exc)
        yield

    app = FastAPI(title="Подбор подрядчиков", version="0.1.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )

    @app.exception_handler(CatalogNotReadyError)
    async def catalog_not_ready(_request, exc: CatalogNotReadyError):
        return JSONResponse(status_code=503, content={"detail": str(exc)})

    @app.exception_handler(RequestValidationError)
    async def invalid_request(_request, exc: RequestValidationError):
        # Do not echo raw invalid values: non-finite numbers (e.g. 1e309)
        # cannot be serialized as JSON and would turn a validation error into 500.
        errors = [
            {key: error[key] for key in ("loc", "msg", "type")}
            for error in exc.errors()
        ]
        return JSONResponse(status_code=422, content={"detail": errors})

    @app.get("/api/health")
    def health() -> dict:
        return {"status": "ok", "profiles": catalog_size(db_path)}

    @app.post("/api/recommend")
    @app.post("/api/recommendations")
    def recommendations(payload: RecommendationRequest) -> dict:
        result = recommend(payload.model_dump(mode="json"), db_path=db_path)
        return _recommendation_response(result)

    return app


app = create_app()
