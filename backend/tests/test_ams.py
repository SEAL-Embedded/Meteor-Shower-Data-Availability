"""AMS fireball scraping.

Parsing is tested against markup captured from the live site: a browse listing, an event page, and
the same event page with its trajectory removed. Nothing here touches the network -- the adapter
tests drive it entirely from cached pages, which is also the path a repeat run takes in practice.
"""

from pathlib import Path

import pytest

from availability.config import Config
from availability.core.geo import haversine_km
from availability.ingest.ams import (
    parse_country_options,
    parse_event_page,
    parse_page,
    rows_to_events,
)
from availability.models import EventKind, SourceStatus
from availability.store import Store

FIXTURES = Path(__file__).parent / "fixtures"
LISTING = (FIXTURES / "ams_browse_events.html").read_text(encoding="utf-8")
EVENT_PAGE = (FIXTURES / "ams_event.html").read_text(encoding="utf-8")
EVENT_NO_TRAJECTORY = (FIXTURES / "ams_event_no_trajectory.html").read_text(encoding="utf-8")

SEATTLE_LAT, SEATTLE_LON = 47.6062, -122.3321

SYNTHETIC = """
<table class="table table-results">
  <thead><tr>{headers}</tr></thead>
  <tbody>
    <tr><td colspan="12" class="event"><strong> August</strong></td></tr>
    <tr>{cells}</tr>
  </tbody>
</table>
<div>Page 1 / 1</div>
"""


def synthetic(headers, cells):
    return SYNTHETIC.format(
        headers="".join(f"<th>{h}</th>" for h in headers),
        cells="".join(f"<td>{c}</td>" for c in cells),
    )


# ------------------------------------------------------------------------------------------
# the listing
# ------------------------------------------------------------------------------------------


class TestListing:
    def test_headers_are_read_from_the_table(self):
        _, _, headers = parse_page(LISTING)
        assert headers[:3] == ["ID", "# of Rep.", "UT Date & Time"]

    def test_page_count_comes_from_the_page_itself(self):
        assert parse_page(LISTING)[1] == 16

    def test_month_heading_rows_are_dropped(self):
        rows, _, _ = parse_page(LISTING)
        assert len(rows) == 6
        assert all("time" in row for row in rows)

    def test_columns_are_located_by_header_not_position(self):
        html = synthetic(
            ["UT Date & Time", "ID", "States", "# of Rep.", "Countries"],
            ["2026-08-14 23:20 UT", "Event 6663-2026", "England", "9", "GB"],
        )
        rows, _, _ = parse_page(html)
        assert rows[0]["time"] == "2026-08-14 23:20 UT"
        assert rows[0]["states"] == "England"

    def test_unrecognisable_headers_yield_nothing_rather_than_guesswork(self):
        rows, _, headers = parse_page(synthetic(["Alpha", "Beta"], ["1", "2"]))
        assert rows == []
        assert headers == ["Alpha", "Beta"]


class TestListingEvents:
    @pytest.fixture
    def events(self):
        rows, _, _ = parse_page(LISTING)
        built, problems = rows_to_events(rows, "ams")
        assert problems == []
        return built

    def test_identity_comes_from_the_link(self, events):
        assert events[0].id == "ams-6663-2026"
        assert events[0].url.endswith("/members/imo_view/event/2026/6663")

    def test_time_is_utc_with_the_suffix_stripped(self, events):
        assert events[0].time.isoformat() == "2026-08-14T23:20:00+00:00"
        assert events[0].kind is EventKind.FIREBALL

    def test_minute_precision_is_declared_as_uncertainty(self, events):
        assert events[0].time_uncertainty_s == 60.0

    def test_location_reads_as_a_place(self, events):
        assert events[0].location.label == "England, Scotland (GB)"

    def test_listing_alone_carries_no_magnitude_or_duration(self, events):
        """Both live on the event's own page; the listing must not invent them."""
        assert all(event.magnitude is None for event in events)
        assert all(event.duration_s is None for event in events)

    def test_a_row_without_a_time_is_reported_not_dropped_silently(self):
        events, problems = rows_to_events([{"id": "Event 1-2026", "href": "/x/2026/1"}], "ams")
        assert events == [] and "no UT time" in problems[0]


# ------------------------------------------------------------------------------------------
# the event page
# ------------------------------------------------------------------------------------------


class TestEventPage:
    def test_position_comes_from_the_computed_epicentre(self):
        detail = parse_event_page(EVENT_PAGE)
        assert detail["latitude"] == pytest.approx(54.668314)
        assert detail["longitude"] == pytest.approx(-2.040307)

    def test_magnitude_prefers_the_computed_average(self):
        assert parse_event_page(EVENT_PAGE)["magnitude"] == pytest.approx(-8.8889)

    def test_duration_is_averaged_from_witness_estimates(self):
        """AMS computes no average duration, so this is the mean of what witnesses reported."""
        duration = parse_event_page(EVENT_PAGE)["duration_s"]
        assert duration is not None and 1.0 < duration < 30.0

    def test_report_count_is_read(self):
        assert parse_event_page(EVENT_PAGE)["report_count"] == 9.0

    def test_without_a_trajectory_it_falls_back_to_the_witness_centre(self):
        detail = parse_event_page(EVENT_NO_TRAJECTORY)
        assert detail["latitude"] is not None
        # The witness centre should land near the epicentre the trajectory reports.
        assert haversine_km(54.668314, -2.040307, detail["latitude"], detail["longitude"]) < 50

    def test_without_a_trajectory_magnitude_comes_from_witnesses(self):
        assert parse_event_page(EVENT_NO_TRAJECTORY)["magnitude"] is not None

    def test_an_unrelated_page_yields_nothing_rather_than_failing(self):
        detail = parse_event_page("<html><body><p>Nothing here</p></body></html>")
        assert all(value is None for value in detail.values())


class TestGeo:
    def test_a_known_separation(self):
        # Seattle to New York, about 3,870 km.
        assert haversine_km(SEATTLE_LAT, SEATTLE_LON, 40.7128, -74.0060) == pytest.approx(
            3870, abs=40
        )

    def test_zero_distance(self):
        assert haversine_km(SEATTLE_LAT, SEATTLE_LON, SEATTLE_LAT, SEATTLE_LON) == 0

    def test_the_captured_event_is_far_outside_north_america(self):
        detail = parse_event_page(EVENT_PAGE)
        distance = haversine_km(
            SEATTLE_LAT, SEATTLE_LON, detail["latitude"], detail["longitude"]
        )
        assert distance == pytest.approx(7345, abs=50)


# ------------------------------------------------------------------------------------------
# the adapter
# ------------------------------------------------------------------------------------------


CONFIG_TOML = """
[[sources]]
id = "ams"
adapter = "ams_fireballs"
kind = "event"
name = "American Meteor Society"
url = "https://fireball.amsmeteors.org/"
attribution = "Fireball reports courtesy of the American Meteor Society"
years = [2026]
countries = ["US"]
cache_dir = "cache"
delay_s = 0
{extra}
"""


@pytest.fixture
def make_config(tmp_path):
    def build(**extra) -> Config:
        lines = "\n".join(f"{key} = {value}" for key, value in extra.items())
        (tmp_path / "config.toml").write_text(CONFIG_TOML.format(extra=lines), encoding="utf-8")
        return Config.load(tmp_path / "config.toml")

    (tmp_path / "cache").mkdir()
    return build


def cache_listing(tmp_path, html: str, page: int = 1, country: str = "US") -> None:
    name = f"events-2026-{country}-p{page:03d}.html"
    (tmp_path / "cache" / name).write_text(html, encoding="utf-8")


def cache_detail(tmp_path, number: int, html: str, year: int = 2026) -> None:
    (tmp_path / "cache" / f"event-{year}-{number}.html").write_text(html, encoding="utf-8")


ONE_PAGE = LISTING.replace("Page 1 / 16", "Page 1 / 1")


class TestAdapterWithoutDetails:
    def test_a_fresh_cache_is_used_without_any_request(self, make_config, tmp_path):
        cache_listing(tmp_path, ONE_PAGE)
        store = Store.build(make_config(fetch_details="false"))
        assert store.sources[0].status is SourceStatus.OK
        assert "served entirely from cache" in store.sources[0].detail
        assert len(store.events) == 6

    def test_every_page_the_table_claims_is_read(self, make_config, tmp_path):
        cache_listing(tmp_path, LISTING.replace("Page 1 / 16", "Page 1 / 2"), page=1)
        cache_listing(tmp_path, LISTING.replace("Page 1 / 16", "Page 2 / 2"), page=2)
        store = Store.build(make_config(fetch_details="false"))
        # Both cached pages hold the same events; an event listed twice is kept once.
        assert len(store.events) == 6

    def test_a_table_that_stopped_matching_is_reported(self, make_config, tmp_path):
        cache_listing(tmp_path, synthetic(["Alpha", "Beta"], ["1", "2"]))
        store = Store.build(make_config(fetch_details="false"))
        assert store.sources[0].status is SourceStatus.ERROR
        assert "headers not recognised" in store.sources[0].detail


class TestAdapterDetails:
    def test_cached_detail_pages_fill_in_the_missing_fields(self, make_config, tmp_path):
        cache_listing(tmp_path, ONE_PAGE)
        cache_detail(tmp_path, 6663, EVENT_PAGE)
        store = Store.build(make_config(max_detail_fetches=0))
        enriched = next(event for event in store.events if event.source_ref == "6663-2026")
        assert enriched.magnitude == pytest.approx(-8.8889)
        assert enriched.duration_s is not None
        assert enriched.location.latitude == pytest.approx(54.668314)

    def test_events_beyond_the_budget_are_deferred_not_fetched(self, make_config, tmp_path):
        cache_listing(tmp_path, ONE_PAGE)
        cache_detail(tmp_path, 6663, EVENT_PAGE)
        store = Store.build(make_config(max_detail_fetches=0))
        # Five of the six have no cached page; with a zero budget none of them is requested.
        assert "5 still awaiting detail" in store.sources[0].detail

    def test_a_deferred_backlog_is_never_silently_hidden(self, make_config, tmp_path):
        cache_listing(tmp_path, ONE_PAGE)
        store = Store.build(make_config(max_detail_fetches=0))
        assert "awaiting detail" in store.sources[0].detail
        assert store.sources[0].status is SourceStatus.OK


COUNTRY_SELECT = """
<select id="country" class="form-control submit-onchange" name="country">
<option value="-1" >All countries</option>
<option value="40|Canada" >Canada</option>
<option value="142|Mexico" >Mexico</option>
<option value="235|United Kingdom" >United Kingdom</option>
<option value="236|United States" >United States</option>
</select>
"""

EMPTY_LISTING = """
<table class="table table-results">
  <thead><tr><th>ID</th><th># of Rep.</th><th>UT Date &amp; Time</th>
  <th>Countries</th><th>States</th></tr></thead>
  <tbody></tbody>
</table>
<div>Page 1 / 1</div>
"""


class TestCountryOptions:
    """The control is keyed by name, not by ISO code.

    Passing a bare code produced a page with no rows and no error, so a whole year silently
    imported as nothing. Both halves of that failure are covered here.
    """

    def test_options_are_read_from_the_page(self):
        options = parse_country_options(COUNTRY_SELECT)
        assert options["united states"] == "236|United States"
        assert options["canada"] == "40|Canada"

    def test_the_all_countries_placeholder_is_not_an_option(self):
        assert "-1" not in parse_country_options(COUNTRY_SELECT).values()

    def test_a_page_without_the_control_yields_nothing(self):
        assert parse_country_options("<html><body>no select here</body></html>") == {}

    def _adapter(self, make_config, tmp_path):
        from availability.ingest.ams import AmsFireballAdapter

        (tmp_path / "cache" / "country-options.html").write_text(
            COUNTRY_SELECT, encoding="utf-8"
        )
        config = make_config(fetch_details="false")
        return AmsFireballAdapter(config, config.sources[0])

    def test_an_iso_code_resolves_to_the_pages_own_value(self, make_config, tmp_path):
        adapter = self._adapter(make_config, tmp_path)
        assert adapter._resolve_country("US") == "236|United States"
        assert adapter._resolve_country("MX") == "142|Mexico"

    def test_a_full_name_resolves_too(self, make_config, tmp_path):
        assert self._adapter(make_config, tmp_path)._resolve_country("Canada") == "40|Canada"

    def test_a_raw_option_value_passes_straight_through(self, make_config, tmp_path):
        adapter = self._adapter(make_config, tmp_path)
        assert adapter._resolve_country("236|United States") == "236|United States"

    def test_an_unmatched_country_is_refused_rather_than_sent_anyway(self, make_config, tmp_path):
        from availability.ingest.ams import AmsRequestError

        adapter = self._adapter(make_config, tmp_path)
        with pytest.raises(AmsRequestError, match="does not match any option"):
            adapter._resolve_country("Atlantis")


class TestBoundedRead:
    """An unbounded read lets the far end decide how much memory this process spends."""

    def test_an_oversized_response_is_abandoned(self, make_config, tmp_path, monkeypatch):
        import urllib.request

        from availability.ingest import ams

        # The ceiling is lowered rather than the response inflated, so the test costs bytes
        # instead of megabytes.
        monkeypatch.setattr(ams, "MAX_RESPONSE_BYTES", 64)

        class Endless:
            def read(self, size=-1):
                return b"x" * (size if size and size > 0 else 1024)

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: Endless())
        store = Store.build(make_config(fetch_details="false"))
        assert store.sources[0].status is SourceStatus.ERROR
        assert "exceeded" in store.sources[0].detail

    def test_the_ceiling_is_far_above_a_real_page(self):
        from availability.ingest import ams

        assert len(LISTING) < ams.MAX_RESPONSE_BYTES / 100


class TestEmptyListing:
    def test_a_listing_that_matched_nothing_is_reported(self, make_config, tmp_path):
        """Zero events for a whole year is a broken filter far more often than a quiet year."""
        cache_listing(tmp_path, EMPTY_LISTING)
        store = Store.build(make_config(fetch_details="false"))
        assert store.events == []
        assert store.sources[0].status is SourceStatus.ERROR
        assert "matched no events" in store.sources[0].detail


class TestRangeFilter:
    def test_an_event_beyond_the_radius_is_dropped(self, make_config, tmp_path):
        cache_listing(tmp_path, ONE_PAGE)
        cache_detail(tmp_path, 6663, EVENT_PAGE)  # England, ~7,345 km from Seattle
        store = Store.build(
            make_config(
                max_detail_fetches=0,
                origin_lat=SEATTLE_LAT,
                origin_lon=SEATTLE_LON,
                max_distance_km=5000,
            )
        )
        assert all(event.source_ref != "6663-2026" for event in store.events)
        assert "beyond 5000 km" in store.sources[0].detail

    def test_the_same_event_is_kept_when_the_radius_reaches_it(self, make_config, tmp_path):
        cache_listing(tmp_path, ONE_PAGE)
        cache_detail(tmp_path, 6663, EVENT_PAGE)
        store = Store.build(
            make_config(
                max_detail_fetches=0,
                origin_lat=SEATTLE_LAT,
                origin_lon=SEATTLE_LON,
                max_distance_km=8000,
            )
        )
        assert any(event.source_ref == "6663-2026" for event in store.events)

    def test_an_event_with_no_position_is_kept_and_counted(self, make_config, tmp_path):
        """A missing coordinate is not evidence of distance, so it must not exclude anything."""
        cache_listing(tmp_path, ONE_PAGE)
        store = Store.build(
            make_config(
                max_detail_fetches=0,
                origin_lat=SEATTLE_LAT,
                origin_lon=SEATTLE_LON,
                max_distance_km=5000,
            )
        )
        assert len(store.events) == 6
        assert "6 kept with no determinable position" in store.sources[0].detail

    def test_no_origin_means_no_filtering(self, make_config, tmp_path):
        cache_listing(tmp_path, ONE_PAGE)
        cache_detail(tmp_path, 6663, EVENT_PAGE)
        store = Store.build(make_config(max_detail_fetches=0))
        assert len(store.events) == 6
