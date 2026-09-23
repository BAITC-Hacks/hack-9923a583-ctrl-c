"""Regression checks from the hackathon review, using an isolated CSV catalog.

Run from the project root: python -m unittest discover -s backend/tests -v
No test reads or modifies the application's working SQLite database.
"""

from contextlib import closing
import csv
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest

from fastapi.testclient import TestClient

from backend.db import initialize_database
from backend.import_dataset import DEFAULT_CSV_PATH, import_csv
from backend.main import create_app
from backend.matching import minimum_score, rejection_reason, score_candidate


def read_source_profiles():
    """Independent expectations read directly from the supplied CSV."""
    with DEFAULT_CSV_PATH.open(encoding="utf-8-sig", newline="") as source:
        profiles = {}
        for row in csv.DictReader(source):
            for field in ("categories", "event_formats", "languages", "busy_dates"):
                row[field] = row[field].split("|") if row[field] else []
            row["price_from_kzt"] = int(row["price_from_kzt"]) if row["price_from_kzt"] else None
            row["max_hours"] = float(row["max_hours"]) if row["max_hours"] else None
            for field in ("synthetic", "price_imputed", "city_imputed"):
                row[field] = row[field].lower() == "true"
            profiles[row["id"]] = row
    return profiles


SOURCE_PROFILES = read_source_profiles()
REQUEST = {
    "city": "Алматы",
    "date": "2026-11-14",
    "event_format": "свадьба",
    "category": "Ведущий",
    "budget": 1_000_000,
}


class CatalogAvailabilityTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.db_path = Path(self.directory.name) / "catalog" / "vendors.sqlite3"

    def assert_unavailable(self):
        with TestClient(create_app(self.db_path)) as client:
            for endpoint in ("/api/health", "/api/recommend", "/api/recommendations"):
                with self.subTest(endpoint=endpoint):
                    response = client.get(endpoint) if endpoint.endswith("health") else client.post(endpoint, json=REQUEST)
                    self.assertEqual(response.status_code, 503)
                    self.assertIn("python backend/import_dataset.py", response.json()["detail"])

    def test_missing_database_is_not_created_by_reads(self):
        self.assert_unavailable()
        self.assertFalse(self.db_path.exists())
        self.assertFalse(self.db_path.parent.exists())

    def test_database_without_schema_is_unavailable(self):
        self.db_path.parent.mkdir()
        with closing(sqlite3.connect(self.db_path)):
            pass
        self.assert_unavailable()

    def test_database_with_incomplete_schema_is_unavailable(self):
        self.db_path.parent.mkdir()
        with closing(sqlite3.connect(self.db_path)) as connection, connection:
            connection.execute("CREATE TABLE vendors (id TEXT)")
            connection.execute("INSERT INTO vendors VALUES ('incomplete')")
        self.assert_unavailable()

    def test_empty_catalog_is_unavailable(self):
        initialize_database(self.db_path)
        self.assert_unavailable()

    def test_corrupt_database_is_unavailable(self):
        self.db_path.parent.mkdir()
        self.db_path.write_bytes(b"not a SQLite catalog")
        self.assert_unavailable()

    def test_import_recovers_same_running_app(self):
        with TestClient(create_app(self.db_path)) as client:
            self.assertEqual(client.get("/api/health").status_code, 503)
            self.assertEqual(import_csv(DEFAULT_CSV_PATH, self.db_path), 66)
            self.assertEqual(client.get("/api/health").json(), {"status": "ok", "profiles": 66})
            self.assertEqual(client.post("/api/recommend", json=REQUEST).json()["status"], "success")
            # Removing an imported catalog must not leave health reporting OK.
            self.db_path.unlink()
            self.assertEqual(client.get("/api/health").status_code, 503)
            self.assertFalse(self.db_path.exists())


class RecommendationApiTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.db_path = Path(self.directory.name) / "vendors.sqlite3"
        self.assertEqual(import_csv(DEFAULT_CSV_PATH, self.db_path), len(SOURCE_PROFILES))
        self.client = TestClient(create_app(self.db_path))
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)

    def recommend(self, **changes):
        payload = REQUEST | changes
        response = self.client.post("/api/recommend", json=payload)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_nonpositive_budget_rejected(self):
        for budget in (0, -1):
            with self.subTest(budget=budget):
                response = self.client.post("/api/recommend", json=REQUEST | {"budget": budget})
                self.assertEqual(response.status_code, 422)

    def test_unknown_event_format_rejected(self):
        response = self.client.post("/api/recommend", json=REQUEST | {"event_format": "несуществующий формат"})
        self.assertEqual(response.status_code, 422)

    def test_dates_outside_window_rejected(self):
        for day in ("2026-09-22", "2027-01-01"):
            with self.subTest(day=day):
                response = self.client.post("/api/recommend", json=REQUEST | {"date": day})
                self.assertEqual(response.status_code, 422)

    def test_both_calendar_boundaries_accepted(self):
        for day in ("2026-09-23", "2026-12-31"):
            with self.subTest(day=day):
                self.recommend(date=day)

    def test_invalid_duration_rejected(self):
        for duration in (0, -1, "Infinity", "-Infinity", "NaN"):
            with self.subTest(duration=duration):
                response = self.client.post("/api/recommend", json=REQUEST | {"duration": duration})
                self.assertEqual(response.status_code, 422)

    def test_overflowing_json_number_returns_validation_error(self):
        # 1e309 is a valid JSON number but overflows Python's float. The error
        # response must remain JSON-serializable instead of causing HTTP 500.
        payload = json.dumps(REQUEST)[:-1] + ', "duration": 1e309}'
        response = self.client.post(
            "/api/recommend", content=payload,
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["detail"][0]["loc"], ["body", "duration"])

    def test_whitespace_only_category_rejected(self):
        response = self.client.post("/api/recommend", json=REQUEST | {"category": "   "})
        self.assertEqual(response.status_code, 422)

    def test_legacy_endpoint_has_same_contract(self):
        expected = self.recommend()
        actual = self.client.post("/api/recommendations", json=REQUEST)
        self.assertEqual(actual.status_code, 200)
        self.assertEqual(actual.json(), expected)

    def test_dense_category_returns_three_stable_ranked_cards(self):
        first = self.recommend(duration=10, language="русский")
        second = self.recommend(duration=10, language="русский")
        self.assertEqual(first, second)
        self.assertEqual(first["status"], "success")
        cards = first["results"]
        self.assertEqual(len(cards), 3)
        self.assertEqual([(item["id"], item["score"]) for item in cards], [
            ("HK-27222", 27), ("HK-42352", 27), ("HK-44923", 22),
        ])
        self.assertEqual(cards, sorted(cards, key=lambda item: (-item["score"], item["id"])))
        for item in cards:
            self.assertEqual(item["score"], sum(item["score_breakdown"].values()))

    def test_returned_cards_pass_all_hard_conditions_in_source(self):
        for day, category, budget in (
            ("2026-11-14", "Ведущий", 1_000_000),
            ("2026-09-23", "Фотограф", 1_000_000),
            ("2026-11-14", "Банкетный зал", 4_000_000),
        ):
            with self.subTest(day=day, category=category):
                response = self.recommend(date=day, category=category, budget=budget)
                self.assertEqual(response["status"], "success")
                self.assertTrue(response["results"])
                self.assertLessEqual(len(response["results"]), 3)
                for card in response["results"]:
                    source = SOURCE_PROFILES[card["id"]]
                    self.assertEqual(source["city"], REQUEST["city"])
                    self.assertIn(category, source["categories"])
                    self.assertIn(REQUEST["event_format"], source["event_formats"])
                    self.assertNotIn(day, source["busy_dates"])
                    if source["price_from_kzt"] is not None:
                        self.assertLessEqual(source["price_from_kzt"] * 100, budget * 130)
                    self.assertIn(day, card["explanation"])
                    self.assertEqual(card["description"], source["description"])
                    self.assertEqual(card["synthetic"], source["synthetic"])

    def test_rare_category_explains_fewer_than_three(self):
        response = self.recommend(city="Астана", category="Флорист")
        self.assertEqual(response["status"], "success")
        self.assertEqual([item["id"] for item in response["results"]], ["HK-90002"])
        self.assertEqual(response["total_candidates"], 1)
        self.assertIn("всего 1 профиль", response["message"])

    def test_city_without_category_has_distinct_status(self):
        self.assertFalse(any(row["city"] == "Астана" and "Декоратор" in row["categories"] for row in SOURCE_PROFILES.values()))
        response = self.recommend(city="Астана", category="Декоратор")
        self.assertEqual(response["status"], "category_not_found")
        self.assertEqual(response["results"], [])
        self.assertEqual(response["total_candidates"], 0)
        self.assertIn("нет подрядчиков", response["message"])

    def test_busy_date_excludes_candidate_and_explains_empty_result(self):
        self.assertIn("2026-12-26", SOURCE_PROFILES["HK-90002"]["busy_dates"])
        available = self.recommend(city="Астана", category="Флорист")
        busy = self.recommend(city="Астана", category="Флорист", date="2026-12-26")
        self.assertEqual(available["status"], "success")
        self.assertEqual(busy["status"], "no_matches")
        self.assertEqual(busy["results"], [])
        self.assertEqual(busy["total_candidates"], 1)
        self.assertEqual(busy["rejected"], {"busy": 1})
        self.assertIn("занят", busy["message"])

    def test_unsupported_format_excludes_florist(self):
        source = SOURCE_PROFILES["HK-90002"]
        self.assertNotIn("той", source["event_formats"])
        self.assertNotIn(REQUEST["date"], source["busy_dates"])
        response = self.recommend(city="Астана", category="Флорист", event_format="той")
        self.assertEqual(response["status"], "no_matches")
        self.assertEqual(response["results"], [])
        self.assertEqual(response["rejected"], {"format": 1})
        self.assertIn("не берёт выбранный формат", response["message"])

    def test_one_tenge_budget_excludes_priced_florist(self):
        self.assertEqual(SOURCE_PROFILES["HK-90002"]["price_from_kzt"], 300_000)
        response = self.recommend(city="Астана", category="Флорист", budget=1)
        self.assertEqual(response["status"], "no_matches")
        self.assertEqual(response["results"], [])
        self.assertEqual(response["rejected"], {"budget": 1})
        self.assertIn("30%", response["message"])

    def test_allowed_budget_overrun_is_visible_with_lower_score(self):
        response = self.recommend(city="Астана", category="Флорист", budget=250_000)
        self.assertEqual(response["status"], "success")
        card = response["results"][0]
        self.assertEqual(card["score_breakdown"]["budget"], 3)
        self.assertIn("выше бюджета на 50 000", card["explanation"])
        self.assertNotIn("В бюджете", card["matches"])

    def test_language_mismatch_remains_soft(self):
        source = SOURCE_PROFILES["HK-90002"]
        self.assertNotIn("английский", source["languages"])
        response = self.recommend(city="Астана", category="Флорист", language="английский")
        self.assertEqual(response["status"], "success")
        card = response["results"][0]
        self.assertEqual(card["score_breakdown"]["language"], -1)
        self.assertIn("не указал язык «английский»", card["explanation"].lower())
        self.assertNotIn("Язык: английский", card["matches"])

    def test_short_duration_remains_soft(self):
        self.assertEqual(SOURCE_PROFILES["HK-44923"]["max_hours"], 8)
        response = self.recommend(duration=10, language="русский")
        card = next(item for item in response["results"] if item["id"] == "HK-44923")
        self.assertEqual(card["score_breakdown"]["duration"], 5)
        self.assertIn("меньше запрошенных 10", card["explanation"])
        self.assertNotIn("Длительность подходит", card["matches"])

    def test_optional_fields_do_not_add_unrequested_points(self):
        response = self.recommend()
        for card in response["results"]:
            self.assertEqual(card["max_score"], 14)
            self.assertNotIn("duration", card["score_breakdown"])
            self.assertNotIn("language", card["score_breakdown"])

    def test_reported_duplicate_explanations_use_distinct_source_facts(self):
        response = self.recommend(date="2026-10-10")
        cards = {card["id"]: card for card in response["results"]}
        self.assertEqual(set(cards), {"HK-27222", "HK-77838"})
        goku = cards["HK-27222"]["explanation"]
        howl = cards["HK-77838"]["explanation"]
        self.assertIn("опытом более 12 лет", goku)
        self.assertIn("ненавязчивой подачей", howl)
        for card in cards.values():
            self.assertIn("2026-10-10", card["explanation"])
        self.assertNotEqual(goku.replace(cards["HK-27222"]["name"], ""), howl.replace(cards["HK-77838"]["name"], ""))


class ScoreBoundaryTests(unittest.TestCase):
    def setUp(self):
        # Isolated copies let each exact monetary boundary be exercised without
        # adding fabricated profiles to the real catalog or altering the CSV.
        self.vendor = dict(SOURCE_PROFILES["HK-90002"])
        self.request = {
            "city": "Астана", "category": "Флорист", "date": "2026-11-14",
            "event_type": "свадьба", "budget_kzt": 100_000,
        }

    def test_equal_and_lower_prices_receive_ten_points(self):
        for price in (1, 50_000, 100_000):
            with self.subTest(price=price):
                vendor = self.vendor | {"price_from_kzt": price}
                self.assertIsNone(rejection_reason(vendor, self.request))
                self.assertEqual(score_candidate(vendor, self.request)["score_breakdown"]["budget"], 10)

    def test_every_started_three_percent_band_loses_one_point(self):
        expected_bands = [
            (100_001, 103_000, 9), (103_001, 106_000, 8),
            (106_001, 109_000, 7), (109_001, 112_000, 6),
            (112_001, 115_000, 5), (115_001, 118_000, 4),
            (118_001, 121_000, 3), (121_001, 124_000, 2),
            (124_001, 127_000, 1), (127_001, 130_000, 0),
        ]
        for lower, upper, expected in expected_bands:
            for price in (lower, upper):
                with self.subTest(price=price):
                    vendor = self.vendor | {"price_from_kzt": price}
                    self.assertIsNone(rejection_reason(vendor, self.request))
                    self.assertEqual(score_candidate(vendor, self.request)["score_breakdown"]["budget"], expected)

    def test_thirty_percent_plus_one_tenge_is_excluded(self):
        vendor = self.vendor | {"price_from_kzt": 130_001}
        self.assertEqual(rejection_reason(vendor, self.request), "budget")

    def test_null_price_is_unknown_rather_than_free(self):
        vendor = self.vendor | {"price_from_kzt": None}
        card = score_candidate(vendor, self.request)
        self.assertEqual(card["score_breakdown"]["budget"], 0)
        self.assertIsNone(card["price_from_kzt"])
        self.assertNotIn("В бюджете", card["matches"])
        self.assertIn("Цена не указана", card["explanation"])

    def test_null_duration_means_presence_not_required(self):
        self.assertIsNone(self.vendor["max_hours"])
        request = self.request | {"budget_kzt": 1_000_000, "duration_hours": 20}
        card = score_candidate(self.vendor, request)
        self.assertEqual(card["score_breakdown"]["duration"], 10)
        self.assertIn("не привязана ко времени присутствия", card["explanation"])

    def test_agreed_whole_point_threshold(self):
        self.assertEqual(minimum_score(27), 6)
        self.assertEqual(minimum_score(14), 3)


if __name__ == "__main__":
    unittest.main()
