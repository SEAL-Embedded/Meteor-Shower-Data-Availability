"""The HTTP API, exercised through Flask's test client.

These cover the surface a front end actually integrates against: the endpoints, the filters, the
error shapes, and the cross-origin rules that decide whether a browser on another origin may call
this at all.
"""

import pytest

from availability.app import create_app
from availability.config import Config
from availability.models import SCHEMA_VERSION

COVERAGE_CSV = """instrument_id,start,end,quality,note
nimbustrace-seattle,2024-08-11T23:33:00Z,2024-08-12T13:29:00Z,good,
supersid-seattle,2024-08-12T01:12:00Z,2024-08-12T09:34:00Z,good,
"""

EVENTS_CSV = """kind,time,label,location,magnitude,duration_s,source_ref
fireball,2024-08-12T05:00:00Z,Covered fireball,Washington,-11,7.5,aaa
fireball,2024-08-12T20:00:00Z,Uncovered fireball,Oregon,-9,3.5,bbb
rocket_launch,2024-08-12T06:00:00Z,A launch,Florida,,,ccc
"""

CONFIG_TOML = """
[api]
allowed_origins = ["https://seal-embedded.github.io"]

[[instruments]]
id = "nimbustrace-seattle"
name = "NimbusTrace"
kind = "vlf"
system = "nimbustrace"

[[instruments]]
id = "supersid-seattle"
name = "SuperSID"
kind = "vlf"
system = "supersid"

[[sources]]
id = "manual-coverage"
adapter = "csv_coverage"
kind = "coverage"
name = "Coverage"
path = "coverage.csv"

[[sources]]
id = "manual-events"
adapter = "csv_events"
kind = "event"
name = "Events"
path = "events.csv"
"""


@pytest.fixture
def client(tmp_path):
    (tmp_path / "coverage.csv").write_text(COVERAGE_CSV, encoding="utf-8")
    (tmp_path / "events.csv").write_text(EVENTS_CSV, encoding="utf-8")
    (tmp_path / "config.toml").write_text(CONFIG_TOML, encoding="utf-8")
    app = create_app(Config.load(tmp_path / "config.toml"))
    app.config.update(TESTING=True)
    return app.test_client()


def body(response):
    return response.get_json()


class TestBasics:
    def test_health(self, client):
        response = client.get("/api/v1/health")
        assert response.status_code == 200
        assert body(response) == {"status": "ok", "schema_version": SCHEMA_VERSION}

    def test_index_lists_instruments_sources_and_years(self, client):
        payload = body(client.get("/api/v1/index"))
        assert payload["schema_version"] == SCHEMA_VERSION
        assert payload["years"] == [2024]
        assert {i["id"] for i in payload["instruments"]} == {
            "nimbustrace-seattle",
            "supersid-seattle",
        }
        assert {s["id"] for s in payload["sources"]} == {"manual-coverage", "manual-events"}

    def test_status_reports_what_is_recording_now(self, client):
        payload = body(client.get("/api/v1/status"))
        assert payload["as_of"].endswith("Z")
        assert payload["degree"] == 0  # the sample record is from 2024

    def test_an_unknown_route_returns_json_not_html(self, client):
        response = client.get("/api/v1/nonsense")
        assert response.status_code == 404
        assert body(response)["error"] == "not_found"


class TestYears:
    def test_a_year_carries_the_derived_products(self, client):
        payload = body(client.get("/api/v1/years/2024"))
        assert payload["coverage"] and payload["segments"]
        assert len(payload["events"]) == len(payload["event_coverage"]) == 3

    def test_a_year_with_no_data_is_a_json_404(self, client):
        response = client.get("/api/v1/years/2019")
        assert response.status_code == 404
        assert body(response)["error"] == "not_found"


class TestCoverage:
    def test_defaults_to_the_whole_record(self, client):
        payload = body(client.get("/api/v1/coverage"))
        assert len(payload["coverage"]) == 2
        assert payload["segments"]

    def test_a_window_narrows_the_result(self, client):
        response = client.get(
            "/api/v1/coverage?start=2024-08-12T02:00:00Z&end=2024-08-12T03:00:00Z"
        )
        payload = body(response)
        assert len(payload["coverage"]) == 2
        assert all(record["start"].startswith("2024-08-12T02") for record in payload["coverage"])

    def test_an_unreadable_timestamp_is_refused(self, client):
        response = client.get("/api/v1/coverage?start=yesterday&end=today")
        assert response.status_code == 400
        assert body(response)["error"] == "bad_request"

    def test_a_backwards_window_is_refused(self, client):
        response = client.get(
            "/api/v1/coverage?start=2024-08-12T05:00:00Z&end=2024-08-12T01:00:00Z"
        )
        assert response.status_code == 400
        assert "precede" in body(response)["message"]


class TestEventFilters:
    def test_all_events_by_default(self, client):
        assert len(body(client.get("/api/v1/events"))["events"]) == 3

    def test_filter_by_source(self, client):
        payload = body(client.get("/api/v1/events?source=manual-events"))
        assert len(payload["events"]) == 3
        assert {event["source_id"] for event in payload["events"]} == {"manual-events"}

    def test_filter_by_kind(self, client):
        payload = body(client.get("/api/v1/events?kind=fireball"))
        assert len(payload["events"]) == 2
        assert all(event["kind"] == "fireball" for event in payload["events"])

    def test_several_kinds_at_once(self, client):
        payload = body(client.get("/api/v1/events?kind=fireball,rocket_launch"))
        assert len(payload["events"]) == 3

    def test_verdicts_are_filtered_alongside_their_events(self, client):
        payload = body(client.get("/api/v1/events?kind=rocket_launch"))
        assert len(payload["event_coverage"]) == 1
        assert payload["event_coverage"][0]["event_id"] == payload["events"][0]["id"]

    def test_an_unknown_source_is_refused_and_the_valid_ones_named(self, client):
        """An empty list would read as a quiet period rather than a typo."""
        response = client.get("/api/v1/events?source=ams")
        assert response.status_code == 400
        assert "manual-events" in body(response)["message"]

    def test_an_unknown_kind_is_refused(self, client):
        response = client.get("/api/v1/events?kind=comet")
        assert response.status_code == 400
        assert "fireball" in body(response)["message"]

    def test_filters_combine_with_the_time_window(self, client):
        response = client.get(
            "/api/v1/events?kind=fireball&start=2024-08-12T00:00:00Z&end=2024-08-12T12:00:00Z"
        )
        payload = body(response)
        assert len(payload["events"]) == 1
        assert payload["events"][0]["label"] == "Covered fireball"


class TestCrossOrigin:
    def test_a_configured_origin_is_allowed(self, client):
        response = client.get(
            "/api/v1/index", headers={"Origin": "https://seal-embedded.github.io"}
        )
        assert response.headers["Access-Control-Allow-Origin"] == (
            "https://seal-embedded.github.io"
        )

    def test_an_unlisted_origin_gets_no_permission(self, client):
        response = client.get("/api/v1/index", headers={"Origin": "https://example.com"})
        assert "Access-Control-Allow-Origin" not in response.headers

    def test_a_request_without_an_origin_is_unaffected(self, client):
        response = client.get("/api/v1/index")
        assert response.status_code == 200
        assert "Access-Control-Allow-Origin" not in response.headers
