"""Acceptance checks for the current backend and its documented matching rules.

Run from the repository root:
    py -3.14 -B test_hackathon.py
Print the reproducible demo POST bodies and responses:
    py -3.14 -B test_hackathon.py --demo

Uses unittest plus the existing backend dependencies; no pytest/httpx required.
Imports the real CSV into a temporary SQLite database. The production database
is never opened. Requests go through the actual FastAPI ASGI application,
including JSON validation and routing, without starting a network server.
Browser rendering, CORS in a browser and network latency are outside this test.
Failures describe unmet requirements; they are deliberately not marked xfail.
"""

import asyncio
from contextlib import ExitStack, closing
import csv
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import time
import unittest

from backend import db, import_dataset
from backend.main import create_app


ROOT = Path(__file__).resolve().parent
CSV_PATH = ROOT / "hackathon dataset anonymized .csv"
BASE = {
    "city": "Алматы", "date": "2026-11-14", "event_format": "свадьба",
    "category": "Ведущий", "budget": 1000000,
    "duration": None, "language": None,
}
DEMOS = {
    "dense": {**BASE, "duration": 10, "language": "русский"},
    "rare": {**BASE, "city": "Астана", "category": "Флорист"},
    "no_matches": {
        **BASE, "city": "Астана", "category": "Флорист", "date": "2026-12-26",
    },
    # The extra fourth request distinguishes absence from failed eligibility.
    "category_absent": {**BASE, "city": "Астана", "category": "Декоратор"},
}


async def asgi_request(application, payload=None, path="/api/recommend", method="POST"):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    messages = []
    delivered = False

    async def receive():
        nonlocal delivered
        if not delivered:
            delivered = True
            return {"type": "http.request", "body": body, "more_body": False}
        await asyncio.Event().wait()

    async def send(message):
        messages.append(message)

    scope = {
        "type": "http", "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1", "method": method, "scheme": "http",
        "path": path, "raw_path": path.encode(), "query_string": b"",
        "root_path": "", "server": ("testserver", 80), "client": ("127.0.0.1", 1234),
        "headers": [(b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode())],
    }
    async with application.router.lifespan_context(application):
        await asyncio.wait_for(application(scope, receive, send), timeout=10)
    status = next(m["status"] for m in messages if m["type"] == "http.response.start")
    response = b"".join(m.get("body", b"") for m in messages if m["type"] == "http.response.body")
    return status, json.loads(response)


class HackathonAcceptanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.resources = ExitStack()
        cls.addClassCleanup(cls.resources.close)
        directory = cls.resources.enter_context(TemporaryDirectory(prefix="eventmatch-tests-"))
        cls.db_path = Path(directory) / "catalog.sqlite3"
        cls.imported_count = import_dataset.import_csv(CSV_PATH, cls.db_path)
        cls.app = create_app(cls.db_path)
        with CSV_PATH.open(encoding="utf-8-sig", newline="") as source:
            cls.rows = {row["id"]: row for row in csv.DictReader(source)}

    def request(self, payload, path="/api/recommend"):
        return asyncio.run(asgi_request(self.app, payload, path))

    def recommend(self, payload):
        status, response = self.request(payload)
        self.assertEqual(status, 200, response)
        self.assertIn(response["status"], ("success", "category_not_found", "no_matches"))
        self.assertTrue(response["message"].strip())
        self.assertLessEqual(len(response["results"]), 3)
        return response

    def test_dataset_import_preserves_profiles(self):
        self.assertEqual(self.imported_count, 66)
        with db.catalog_connection(self.db_path) as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM vendors").fetchone()[0], 66)
            self.assertEqual(connection.execute("SELECT sum(synthetic) FROM vendors").fetchone()[0], 13)
        import_dataset.import_csv(CSV_PATH, self.db_path)
        with db.catalog_connection(self.db_path) as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM vendors").fetchone()[0], 66)

    def test_demo_dense_category_has_ranked_top_three(self):
        result = self.recommend(DEMOS["dense"])
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["total_candidates"], 10)
        self.assertGreaterEqual(result["eligible_candidates"], 3)
        self.assertEqual(len(result["results"]), 3)
        scores = [card["score"] for card in result["results"]]
        self.assertEqual(scores, [27, 27, 22])
        self.assertEqual(scores, sorted(scores, reverse=True))
        self.assertGreater(len(set(scores)), 1, "Demo must exercise actual ranking, not only ties")

    def test_demo_rare_category_explains_fewer_than_three(self):
        result = self.recommend(DEMOS["rare"])
        self.assertEqual(result["status"], "success")
        self.assertEqual([c["id"] for c in result["results"]], ["HK-90002"])
        self.assertIn("всего 1", result["message"])
        self.assertIs(result["results"][0]["synthetic"], True)
        self.assertIsNone(result["results"][0]["max_hours"])

    def test_demo_existing_category_but_all_busy(self):
        result = self.recommend(DEMOS["no_matches"])
        self.assertEqual(result["status"], "no_matches")
        self.assertEqual(result["total_candidates"], 1)
        self.assertEqual(result["results"], [])
        self.assertEqual(result["rejected"]["busy"], 1)
        self.assertIn("занят", result["message"])

    def test_demo_category_absent_is_a_separate_outcome(self):
        result = self.recommend(DEMOS["category_absent"])
        self.assertEqual(result["status"], "category_not_found")
        self.assertEqual(result["total_candidates"], 0)
        self.assertEqual(result["results"], [])
        self.assertIn("нет подрядчиков", result["message"])

    def test_repeated_request_has_identical_order(self):
        first = self.recommend(DEMOS["dense"])
        second = self.recommend(DEMOS["dense"])
        self.assertEqual(first, second)

    def test_explanations_identify_each_recommended_profile(self):
        result = self.recommend({**BASE, "date": "2026-10-10"})
        self.assertEqual({c["id"] for c in result["results"]}, {"HK-27222", "HK-77838"})
        explanations = [card["explanation"] for card in result["results"]]
        self.assertEqual(len(explanations), len(set(explanations)),
                         "Different profiles have verbatim identical explanations")

    def test_new_date_changes_results_and_explains_availability(self):
        available = self.recommend(DEMOS["rare"])
        busy = self.recommend(DEMOS["no_matches"])
        self.assertNotEqual(available["results"], busy["results"])
        self.assertIn("занят", busy["message"])

    def test_busy_contractors_and_venues_are_never_returned(self):
        for category in ("Ведущий", "Фотограф", "Банкетный зал"):
            for day in ("2026-09-23", "2026-11-14", "2026-12-26"):
                with self.subTest(category=category, date=day):
                    result = self.recommend({**BASE, "category": category, "date": day})
                    for card in result["results"]:
                        row = self.rows[card["id"]]
                        self.assertEqual(row["city"], BASE["city"])
                        self.assertIn(category, row["categories"].split("|"))
                        self.assertNotIn(day, row["busy_dates"].split("|"))
                        self.assertIn(BASE["event_format"], row["event_formats"].split("|"))
                        self.assertLessEqual(int(row["price_from_kzt"]) * 100, BASE["budget"] * 130)
                        self.assertIn(day, card["explanation"])

    def test_card_contract_and_synthetic_flags(self):
        for scenario in ("dense", "rare"):
            result = self.recommend(DEMOS[scenario])
            for card in result["results"]:
                with self.subTest(id=card["id"]):
                    for field in ("name", "categories", "city", "price", "languages", "max_hours",
                                  "explanation", "matches", "synthetic"):
                        self.assertIn(field, card)
                    self.assertTrue(card["explanation"].strip())
                    self.assertIsInstance(card["matches"], list)
                    self.assertIs(card["synthetic"], self.rows[card["id"]]["synthetic"] == "True")

    def test_insufficient_budget_produces_no_matches(self):
        result = self.recommend({**DEMOS["rare"], "budget": 1})
        self.assertEqual(result["status"], "no_matches", "300000 KZT profile returned for 1 KZT budget")
        self.assertEqual(result["results"], [])
        self.assertEqual(result["rejected"], {"budget": 1})

    def test_unsupported_format_produces_no_matches(self):
        result = self.recommend({**DEMOS["rare"], "event_format": "той"})
        self.assertEqual(result["status"], "no_matches", "The only florist does not offer this format")
        self.assertEqual(result["results"], [])
        self.assertEqual(result["rejected"], {"format": 1})

    def test_allowed_overrun_has_penalty_and_honest_explanation(self):
        result = self.recommend({**DEMOS["rare"], "budget": 250000})
        self.assertEqual(result["status"], "success")
        card = result["results"][0]
        self.assertEqual(card["score_breakdown"]["budget"], 3)
        self.assertNotIn("В бюджете", card["matches"])
        self.assertIn("выше бюджета на 50 000", card["explanation"])

    def test_soft_preferences_are_disclosed(self):
        result = self.recommend({**DEMOS["rare"], "language": "английский"})
        self.assertEqual(result["status"], "success")
        card = result["results"][0]
        self.assertEqual(card["score_breakdown"]["language"], -1)
        self.assertIn("не указал язык", card["explanation"])
        self.assertNotIn("Язык: английский", card["matches"])
        short = next(c for c in self.recommend(DEMOS["dense"])["results"] if c["id"] == "HK-44923")
        self.assertIn("меньше запрошенных 10", short["explanation"])
        self.assertNotIn("Длительность подходит", short["matches"])

    def test_score_threshold_at_and_below_boundary(self):
        # Modify only a separate temporary fixture, never the shared demo catalog.
        with TemporaryDirectory(prefix="eventmatch-boundary-") as directory:
            path = Path(directory) / "boundary.sqlite3"
            import_dataset.import_csv(CSV_PATH, path)
            with closing(db.connect(path)) as connection, connection:
                connection.execute("UPDATE vendors SET max_hours = 1 WHERE id = 'HK-90002'")
            application = create_app(path)
            for duration, expected in ((5, "success"), (5.4, "no_matches")):
                with self.subTest(duration=duration):
                    payload = {**DEMOS["rare"], "budget": 250000,
                               "language": "английский", "duration": duration}
                    status, result = asyncio.run(asgi_request(application, payload))
                    self.assertEqual(status, 200)
                    self.assertEqual(result["minimum_score"], 6)
                    self.assertEqual(result["status"], expected)
                    if expected == "success":
                        self.assertEqual(result["results"][0]["score"], 6)
                    else:
                        self.assertEqual(result["rejected"], {"low_score": 1})
                        self.assertEqual(result["results"], [])

    def test_missing_catalog_recovers_after_import(self):
        with TemporaryDirectory(prefix="eventmatch-health-") as directory:
            path = Path(directory) / "missing.sqlite3"
            application = create_app(path)
            for endpoint, method, payload in (("/api/health", "GET", None),
                                               ("/api/recommend", "POST", DEMOS["rare"])):
                status, result = asyncio.run(asgi_request(application, payload, endpoint, method))
                self.assertEqual(status, 503)
                self.assertIn("import_dataset.py", result["detail"])
                self.assertFalse(path.exists())
            import_dataset.import_csv(CSV_PATH, path)
            status, result = asyncio.run(asgi_request(application, path="/api/health", method="GET"))
            self.assertEqual(status, 200)
            self.assertEqual(result, {"status": "ok", "profiles": 66})

    def test_readme_demo_requests(self):
        cases = (
            ({**BASE, "date": "2026-10-13", "event_format": "корпоратив",
              "budget": 800000, "duration": 8, "language": "русский"}, "success", 3),
            ({**BASE, "date": "2026-09-23", "category": "Флорист", "budget": 2000000}, "success", 2),
            ({**BASE, "date": "2026-09-26", "category": "Флорист", "budget": 20000}, "no_matches", 0),
        )
        for payload, expected, count in cases:
            with self.subTest(payload=payload):
                result = self.recommend(payload)
                self.assertEqual(result["status"], expected)
                self.assertEqual(len(result["results"]), count)

    def test_api_rejects_dates_outside_known_calendar(self):
        for day in ("2026-09-22", "2027-01-01"):
            with self.subTest(date=day):
                status, response = self.request({**DEMOS["rare"], "date": day})
                self.assertEqual(status, 422, response)

    def test_api_rejects_zero_budget_like_the_form(self):
        status, response = self.request({**BASE, "budget": 0})
        self.assertEqual(status, 422, response)

    def test_api_rejects_unknown_event_format(self):
        status, response = self.request({**BASE, "event_format": "not-an-event"})
        self.assertEqual(status, 422, response)

    def test_api_rejects_malformed_inputs(self):
        for changes in ({"date": "2026-02-30"}, {"budget": -1}, {"duration": 0},
                        {"city": "unknown"}, {"language": "unknown"}):
            with self.subTest(changes=changes):
                status, _ = self.request({**BASE, **changes})
                self.assertEqual(status, 422)

    def test_api_alias_returns_same_response(self):
        self.assertEqual(self.request(DEMOS["rare"]),
                         self.request(DEMOS["rare"], "/api/recommendations"))

    def test_demo_responses_take_less_than_ten_seconds(self):
        for name, payload in DEMOS.items():
            with self.subTest(name=name):
                start = time.perf_counter()
                self.recommend(payload)
                self.assertLess(time.perf_counter() - start, 10)


def print_demos():
    try:
        HackathonAcceptanceTests.setUpClass()
        for name, payload in DEMOS.items():
            start = time.perf_counter()
            status, response = asyncio.run(asgi_request(HackathonAcceptanceTests.app, payload))
            print(json.dumps({
                "scenario": name, "request": payload, "http_status": status,
                "elapsed_ms": round((time.perf_counter() - start) * 1000, 2),
                "response": response,
            }, ensure_ascii=False, indent=2))
    finally:
        HackathonAcceptanceTests.doClassCleanups()


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    if sys.argv[1:] == ["--demo"]:
        print_demos()
    else:
        unittest.main(verbosity=2)
