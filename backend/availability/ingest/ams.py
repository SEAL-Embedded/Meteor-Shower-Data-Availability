"""American Meteor Society fireball events, read from their public pages.

This replaces a spreadsheet web query that pulled the same table by hand: same page, same filters,
same columns, but on a schedule and landing straight in the record instead of being retyped.

Two stages, because they cost very different amounts
----------------------------------------------------
The browse table is cheap -- one request per fifty events -- and carries the id, report count, UT
time to the minute, countries and states. It does **not** carry magnitude, duration, or coordinates.
Those live on each event's own page, which is one request per event.

So the country filter runs first, server-side, using the page's own control. Only the events that
survive it are fetched individually. Fetching a year of worldwide events one page at a time to then
discard nine tenths of them would be both slow and rude.

The distance filter runs last, because the coordinates it needs only exist on the detail page. This
means the country list is what actually bounds the request count; the radius trims the edges. Set
both deliberately.

Every kept event stores its coordinates, so a different geometry can be applied later -- a
transmitter-to-receiver path rather than a radius around the receiver, say -- without asking AMS for
anything again.

Scraping is brittle by nature: a column reorder upstream silently changes what every field means.
Columns are therefore located by their header text rather than by position, and when the headers stop
matching anything known the source reports what it actually found instead of importing nonsense.

AMS also publishes a documented REST API needing a key issued to scientific organisations, which is
the sturdier route if this ever becomes more trouble than it is worth:
https://www.amsmeteors.org/members/imo_api/
"""

from __future__ import annotations

import json
import re
import statistics
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Callable, Iterable

from ..core.geo import haversine_km
from ..models import Event, EventKind, EventLocation, SourceKind, SourceStatus, now
from .base import Adapter, EventResult, register

BROWSE_URL = "https://fireball.amsmeteors.org/members/imo_view/browse_events"
EVENT_URL = "https://fireball.amsmeteors.org/members/imo_view/event/{year}/{number}"
SITE_ROOT = "https://fireball.amsmeteors.org"

DEFAULT_CACHE_DIR = ".cache/ams"
DEFAULT_MAX_AGE_S = 21600.0  # six hours for listings
DEFAULT_DETAIL_MAX_AGE_S = 604800.0  # a week, while an event is still collecting reports
DEFAULT_SETTLE_DAYS = 60.0  # after which a trajectory is treated as final and cached for good
DEFAULT_MAX_DETAIL_FETCHES = 500
SETTLED_MAX_AGE_S = 315360000.0  # ten years; effectively permanent
COUNTRY_OPTIONS_MAX_AGE_S = 2592000.0  # thirty days; the country list barely changes
MAX_RESPONSE_BYTES = 20 * 1024 * 1024  # a listing page is under 100 kB; this is a sanity ceiling
DEFAULT_TIMEOUT_S = 45.0
DEFAULT_DELAY_S = 2.0
DEFAULT_MIN_REPORTS = 1
DEFAULT_COUNTRIES = ("US", "CA", "MX")
MAX_PAGES = 200
USER_AGENT = "meteor-shower-data-availability/0.1 (instrument availability record)"

#: Browse-table headers we know how to read, mapped to the field they carry.
_BROWSE_FIELDS = {
    "id": "id",
    "# of rep.": "reports",
    "ut date & time": "time",
    "local date & time": "local_time",
    "countries": "countries",
    "states": "states",
}
_BROWSE_REQUIRED = ("id", "time")

#: Per-witness report table on an event's own page.
_REPORT_FIELDS = {"dur.": "duration", "magn.": "magnitude", "location": "location"}
_REPORT_REQUIRED = ("duration", "magnitude")

_EVENT_HREF = re.compile(r"/members/imo_view/event/(?P<year>\d{4})/(?P<number>\d+)")
_PAGE_COUNT = re.compile(r"Page\s+\d+\s*/\s*(?P<total>\d+)", re.IGNORECASE)
_TRAILING_UT = re.compile(r"\s*UTC?\s*$", re.IGNORECASE)
_COUNTRY_SELECT = re.compile(r'<select[^>]*name="country".*?</select>', re.DOTALL | re.IGNORECASE)
_OPTION = re.compile(r'<option[^>]*value="(?P<value>[^"]*)"[^>]*>\s*(?P<label>[^<]*)')
_TRAJECTORY = re.compile(r"trajectory\s*=\s*(?P<body>\[.*?\]|null)\s*;", re.DOTALL)

#: The country control is keyed by name, not by ISO code, so the codes people actually write are
#: translated here before being looked up in the page's own option list.
_COUNTRY_ALIASES = {
    "US": "united states",
    "USA": "united states",
    "CA": "canada",
    "MX": "mexico",
    "GB": "united kingdom",
    "UK": "united kingdom",
}
_OBSERVERS = re.compile(r"observers\s*=\s*(?P<body>\{.*?\})\s*;", re.DOTALL)
_SECONDS = re.compile(r"(?P<value>\d+(?:\.\d+)?)\s*s", re.IGNORECASE)
_SIGNED_NUMBER = re.compile(r"-?\d+(?:\.\d+)?")


@register("ams_fireballs")
class AmsFireballAdapter(Adapter):
    """Scrape AMS fireball events, optionally within range of an instrument.

    Options:

    ``years``
        Years to read, as a list. Defaults to the current UTC year.
    ``min_reports``
        Lowest report count to include. The site defaults to 5, which drops the faint events;
        this asks for 1 so the record is complete.
    ``countries``
        Country codes to query, one request set each, using the page's own filter. Defaults to
        ``["US", "CA", "MX"]``. An empty list queries the whole world, which is a great many more
        requests. **This is the setting that bounds how much work a run does.**
    ``origin_lat`` / ``origin_lon`` / ``max_distance_km``
        Keep only events whose ground position is within ``max_distance_km`` of that point. All
        three must be set for the filter to apply. Events whose position cannot be determined are
        kept, and counted in the source detail, rather than silently discarded.
    ``fetch_details``
        Whether to fetch each event's own page for magnitude, duration and coordinates. Defaults
        to true. Turning it off makes a run far cheaper and leaves those three fields empty.
    ``max_detail_fetches``
        Most event pages a single run may fetch, 500 by default. Cached pages are free and do not
        count, so ``0`` means "use what is already cached and fetch nothing new". A backlog is
        worked through over successive runs rather than in one sitting, and every run reports how
        many are still outstanding. There is no unlimited setting; name a large number if that is
        what you want, so the number of requests a run can make is always visible in the config.
    ``detail_max_age_s`` / ``detail_settle_days``
        How long a recent event's page is reused (a week), and the age past which an event is
        treated as final and its page cached indefinitely (sixty days). A fireball from last year
        is not going to change.
    ``cache_dir`` / ``max_age_s`` / ``delay_s`` / ``timeout_s``
        Cache location, listing cache lifetime, pause between requests, per-request timeout.
    """

    kind = SourceKind.EVENT

    def __init__(self, config, source_config) -> None:
        super().__init__(config, source_config)
        self._options: dict[str, str] | None = None

    def fetch(self) -> EventResult:
        events: list[Event] = []
        problems: list[str] = []
        requests_made = 0

        for year in self._years():
            for country in self._countries():
                try:
                    pages, count = self._pages_for(year, country)
                except AmsRequestError as exc:
                    problems.append(str(exc))
                    continue
                requests_made += count
                found, page_problems = self._events_from_pages(pages, year, country)
                events.extend(found)
                problems.extend(page_problems)

        events = _drop_duplicates(events)
        events, detail_requests, detail_problems, deferred = self._enrich(events)
        requests_made += detail_requests
        problems.extend(detail_problems)

        events, dropped, undetermined = self._within_range(events)

        status, detail = self._outcome(
            events, problems, requests_made, dropped, undetermined, deferred
        )
        return EventResult(source=self.describe(status, detail), events=events)

    def _outcome(
        self,
        events: list[Event],
        problems: list[str],
        requests_made: int,
        dropped: int,
        undetermined: int,
        deferred: int,
    ) -> tuple[SourceStatus, str | None]:
        notes: list[str] = []
        if dropped:
            notes.append(f"{dropped} beyond {self.option('max_distance_km')} km")
        if deferred:
            # Said out loud, because a silently capped run looks identical to a complete one.
            notes.append(
                f"{deferred} still awaiting detail; raise max_detail_fetches or run again"
            )
        if undetermined:
            notes.append(f"{undetermined} kept with no determinable position")
        notes.append(
            f"{requests_made} request(s) made" if requests_made else "served entirely from cache"
        )

        if not events and problems:
            return SourceStatus.ERROR, "; ".join(problems[:3])
        if problems:
            summary = f"{len(problems)} problem(s): " + "; ".join(problems[:3])
            return SourceStatus.STALE, "; ".join([summary, *notes])
        return SourceStatus.OK, "; ".join(notes) or None

    # -- configuration ---------------------------------------------------------------------

    def _years(self) -> list[int]:
        configured = self.option("years")
        if configured:
            return [int(year) for year in configured]
        return [datetime.now(timezone.utc).year]

    def _countries(self) -> list[str | None]:
        configured = self.option("countries", list(DEFAULT_COUNTRIES))
        if not configured:
            return [None]
        return [str(code).strip().upper() for code in configured]

    def _origin(self) -> tuple[float, float, float] | None:
        latitude = self.option("origin_lat")
        longitude = self.option("origin_lon")
        radius = self.option("max_distance_km")
        if latitude is None or longitude is None or radius is None:
            return None
        return float(latitude), float(longitude), float(radius)

    def _cache_dir(self) -> Path:
        return self.config.resolve(self.option("cache_dir", DEFAULT_CACHE_DIR))

    # -- stage one: the listing ------------------------------------------------------------

    def _pages_for(self, year: int, country: str | None) -> tuple[list[tuple[int, str]], int]:
        first, requests_made = self._listing(year, country, 1)
        pages = [(1, first)]
        _, total_pages, _ = parse_page(first)

        if total_pages > MAX_PAGES:
            raise AmsRequestError(
                f"{year} {country or 'worldwide'}: the table reports {total_pages} pages, beyond "
                f"the {MAX_PAGES} cap; raise min_reports or narrow the country list"
            )

        for page_number in range(2, total_pages + 1):
            html, made = self._listing(year, country, page_number)
            requests_made += made
            pages.append((page_number, html))
        return pages, requests_made

    def _events_from_pages(
        self, pages: list[tuple[int, str]], year: int, country: str | None
    ) -> tuple[list[Event], list[str]]:
        events: list[Event] = []
        problems: list[str] = []
        where = f"{year} {country or 'worldwide'}"

        for page_number, html in pages:
            rows, _, headers = parse_page(html)
            if not rows and headers and not _fields_from_headers(headers, _BROWSE_FIELDS, _BROWSE_REQUIRED):
                problems.append(f"{where} page {page_number}: headers not recognised, found {headers}")
                continue
            page_events, page_problems = rows_to_events(rows, self.source_config.id)
            events.extend(page_events)
            problems.extend(f"{where} page {page_number}: {p}" for p in page_problems)

        if not events and not problems:
            # A whole year with nothing in it is far more likely to be a filter that failed to
            # apply than a year in which nobody saw a fireball. Silence here once cost a run.
            problems.append(
                f"{where}: the listing matched no events at all, which usually means a filter "
                f"was not accepted by the page rather than a genuinely empty year"
            )
        return events, problems

    def _country_options(self) -> dict[str, str]:
        """The country control's own option list, read from the page rather than hardcoded.

        Its values are ``id|Name`` pairs whose numbering is the site's business, so they are
        looked up rather than assumed. One request, cached for a month.
        """
        if self._options is None:
            html, _ = self._cached(
                "country-options.html",
                self.option("base_url", BROWSE_URL),
                dict,
                COUNTRY_OPTIONS_MAX_AGE_S,
                "country list",
            )
            self._options = parse_country_options(html)
        return self._options

    def _resolve_country(self, code: str) -> str:
        if "|" in code:
            return code  # already a raw option value
        options = self._country_options()
        name = _COUNTRY_ALIASES.get(code.upper(), code).strip().lower()
        value = options.get(name)
        if value is None:
            sample = ", ".join(sorted(options)[:5]) or "none found"
            raise AmsRequestError(
                f"country {code!r} does not match any option on the page "
                f"(looked for {name!r}; the page offers e.g. {sample})"
            )
        return value

    def _listing(self, year: int, country: str | None, page: int) -> tuple[str, int]:
        def query() -> dict[str, Any]:
            built: dict[str, Any] = {
                "year": year,
                "num_report": self.option("min_reports", DEFAULT_MIN_REPORTS),
            }
            if country:
                built["country"] = self._resolve_country(country)
            if page > 1:
                built["page"] = page
            return built

        return self._cached(
            f"events-{year}-{country or 'all'}-p{page:03d}.html",
            self.option("base_url", BROWSE_URL),
            query,
            float(self.option("max_age_s", DEFAULT_MAX_AGE_S)),
            f"{year} {country or 'worldwide'} page {page}",
        )

    # -- stage two: the detail pages -------------------------------------------------------

    def _enrich(self, events: list[Event]) -> tuple[list[Event], int, list[str], int]:
        """Fill in magnitude, duration and position, within a per-run request budget.

        Cached pages are free and always read. Only pages that must actually be fetched count
        against the budget, so a nightly run steadily works through a backlog instead of doing
        several thousand requests in one sitting. Events not reached this time keep their listing
        fields and get filled in on a later run.
        """
        if not self.option("fetch_details", True):
            return events, 0, [], 0

        budget = int(self.option("max_detail_fetches", DEFAULT_MAX_DETAIL_FETCHES))
        enriched: list[Event] = []
        problems: list[str] = []
        requests_made = 0
        deferred = 0

        for event in events:
            found = _EVENT_HREF.search(event.url or "")
            if not found:
                enriched.append(event)
                continue

            year, number = found.group("year"), found.group("number")
            cache_file = self._cache_dir() / f"event-{year}-{number}.html"
            if not self._is_fresh(cache_file, self._detail_max_age(event)) and (
                requests_made >= budget
            ):
                deferred += 1
                enriched.append(event)
                continue

            try:
                html, made = self._detail(year, number, event)
            except AmsRequestError as exc:
                problems.append(str(exc))
                enriched.append(event)
                continue
            requests_made += made
            enriched.append(_apply_detail(event, parse_event_page(html)))

        return enriched, requests_made, problems, deferred

    def _detail_max_age(self, event: Event) -> float:
        """A settled event never changes again, so its page is cached indefinitely."""
        settle_days = float(self.option("detail_settle_days", DEFAULT_SETTLE_DAYS))
        age_days = (now() - event.time).total_seconds() / 86400.0
        if age_days > settle_days:
            return SETTLED_MAX_AGE_S
        return float(self.option("detail_max_age_s", DEFAULT_DETAIL_MAX_AGE_S))

    def _detail(self, year: str, number: str, event: Event) -> tuple[str, int]:
        return self._cached(
            f"event-{year}-{number}.html",
            EVENT_URL.format(year=year, number=number),
            dict,
            self._detail_max_age(event),
            f"event {number}-{year}",
        )

    # -- stage three: the range filter -----------------------------------------------------

    def _within_range(self, events: list[Event]) -> tuple[list[Event], int, int]:
        origin = self._origin()
        if origin is None:
            return events, 0, 0

        latitude, longitude, radius = origin
        kept: list[Event] = []
        dropped = undetermined = 0

        for event in events:
            position = event.location
            if position is None or position.latitude is None or position.longitude is None:
                undetermined += 1
                kept.append(event)  # never discarded on the strength of a missing coordinate
                continue
            if haversine_km(latitude, longitude, position.latitude, position.longitude) <= radius:
                kept.append(event)
            else:
                dropped += 1
        return kept, dropped, undetermined

    # -- fetching --------------------------------------------------------------------------

    @staticmethod
    def _is_fresh(cache_file: Path, max_age_s: float) -> bool:
        if not cache_file.is_file():
            return False
        age = now() - datetime.fromtimestamp(cache_file.stat().st_mtime, timezone.utc)
        return age.total_seconds() < max_age_s

    def _cached(
        self,
        name: str,
        url: str,
        query_factory: Callable[[], dict[str, Any]],
        max_age_s: float,
        label: str,
    ) -> tuple[str, int]:
        """Read from cache, or fetch.

        The query is built lazily, on purpose: resolving a country name costs its own request, and
        a run served entirely from cache must make no requests at all.
        """
        cache_file = self._cache_dir() / name
        if self._is_fresh(cache_file, max_age_s):
            return cache_file.read_text(encoding="utf-8"), 0

        html = self._request(url, query_factory(), label)
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(html, encoding="utf-8")
        return html, 1

    def _request(self, url: str, query: dict[str, Any], label: str) -> str:
        if query:
            url = f"{url}?{urllib.parse.urlencode(query)}"
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        timeout = float(self.option("timeout_s", DEFAULT_TIMEOUT_S))

        delay = float(self.option("delay_s", DEFAULT_DELAY_S))
        if delay > 0:
            time.sleep(delay)

        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                # Bounded read. An unbounded one trusts the far end about how much memory this
                # process is willing to spend, and a listing page is under 100 kB -- anything
                # near the cap is a redirect loop or an error page, not data.
                payload = response.read(MAX_RESPONSE_BYTES + 1)
                if len(payload) > MAX_RESPONSE_BYTES:
                    raise AmsRequestError(
                        f"{label}: response exceeded {MAX_RESPONSE_BYTES // 1024 // 1024} MB "
                        f"and was abandoned"
                    )
                return payload.decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            raise AmsRequestError(f"{label}: HTTP {exc.code}") from None
        except urllib.error.URLError as exc:
            raise AmsRequestError(f"{label}: unreachable ({exc.reason})") from None
        except TimeoutError:
            raise AmsRequestError(f"{label}: no answer within {timeout:g}s") from None


class AmsRequestError(Exception):
    """A request that failed in a way worth reporting rather than retrying blindly."""


# ------------------------------------------------------------------------------------------
# parsing -- no I/O, so it can be tested against captured markup
# ------------------------------------------------------------------------------------------


class _TableReader(HTMLParser):
    """Collect every ``table-results`` table on a page as (headers, rows).

    Kept per table rather than merged: an event's own page carries more than one, and merging
    their headers would make every column mapping wrong.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._depth = 0
        self._in_results = False
        self._in_head = self._in_cell = False
        self._text: list[str] = []
        self._cells: list[str] = []
        self._href: str | None = None
        self._headers: list[str] = []
        self._rows: list[tuple[list[str], str | None]] = []
        self.tables: list[tuple[list[str], list[tuple[list[str], str | None]]]] = []
        self._all_text: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        attributes = dict(attrs)
        if tag == "table":
            self._depth += 1
            if "table-results" in (attributes.get("class") or ""):
                self._in_results = True
                self._headers, self._rows = [], []
        elif not self._in_results:
            return
        elif tag == "tr":
            # The row's link lives in its first cell, so the href is held for the whole row.
            self._cells, self._href = [], None
        elif tag == "th":
            self._in_head, self._text = True, []
        elif tag == "td":
            self._in_cell, self._text = True, []
        elif tag == "a" and self._in_cell and self._href is None:
            self._href = attributes.get("href")

    def handle_endtag(self, tag: str) -> None:
        if tag == "table":
            self._depth -= 1
            if self._in_results:
                self.tables.append((self._headers, self._rows))
                self._in_results = False
                self._headers, self._rows = [], []
            return
        if not self._in_results:
            return
        if tag == "th" and self._in_head:
            self._headers.append(_squash(self._text))
            self._in_head = False
        elif tag == "td" and self._in_cell:
            self._cells.append(_squash(self._text))
            self._in_cell = False
        elif tag == "tr" and self._cells:
            self._rows.append((self._cells, self._href))
            self._cells, self._href = [], None

    def handle_data(self, data: str) -> None:
        self._all_text.append(data)
        if self._in_cell or self._in_head:
            self._text.append(data)

    @property
    def page_text(self) -> str:
        return " ".join(self._all_text)


def _read_tables(html: str) -> tuple[_TableReader, list]:
    reader = _TableReader()
    reader.feed(html)
    return reader, reader.tables


def _fields_from_headers(
    headers: Iterable[str], known: dict[str, str], required: tuple[str, ...]
) -> dict[int, str]:
    mapping: dict[int, str] = {}
    for index, header in enumerate(headers):
        field = known.get(header.strip().lower())
        if field:
            mapping[index] = field
    if not all(field in mapping.values() for field in required):
        return {}
    return mapping


def _rows_as_dicts(
    table: tuple[list[str], list[tuple[list[str], str | None]]],
    known: dict[str, str],
    required: tuple[str, ...],
) -> list[dict[str, str]]:
    headers, raw_rows = table
    fields = _fields_from_headers(headers, known, required)
    if not fields:
        return []
    rows: list[dict[str, str]] = []
    for cells, href in raw_rows:
        if len(cells) < len(headers) - 3:
            continue  # a heading row spanning the table, not a record
        row = {field: cells[index] for index, field in fields.items() if index < len(cells)}
        if href:
            row["href"] = href
        rows.append(row)
    return rows


def parse_country_options(html: str) -> dict[str, str]:
    """Map each country name on the page to the value its option carries."""
    select = _COUNTRY_SELECT.search(html)
    if not select:
        return {}
    options: dict[str, str] = {}
    for match in _OPTION.finditer(select.group(0)):
        label = match.group("label").strip().lower()
        value = match.group("value").strip()
        if label and value and value != "-1":
            options[label] = value
    return options


def parse_page(html: str) -> tuple[list[dict[str, str]], int, list[str]]:
    """Extract browse-table rows, the total page count, and the table's headers."""
    reader, tables = _read_tables(html)

    found = _PAGE_COUNT.search(reader.page_text)
    total_pages = int(found.group("total")) if found else 1

    for table in tables:
        rows = _rows_as_dicts(table, _BROWSE_FIELDS, _BROWSE_REQUIRED)
        if rows:
            return rows, total_pages, table[0]

    headers = tables[0][0] if tables else []
    return [], total_pages, headers


def parse_event_page(html: str) -> dict[str, Any]:
    """Pull position, magnitude and duration from one event's own page.

    Magnitude prefers the trajectory's computed average over the mean of witness estimates.
    Duration has no computed average, so it is the mean of what witnesses reported -- which is
    what the number on that page means too.
    """
    detail: dict[str, Any] = {
        "latitude": None,
        "longitude": None,
        "magnitude": None,
        "duration_s": None,
        "report_count": None,
    }

    trajectory = _trajectory(html)
    if trajectory:
        detail["latitude"], detail["longitude"] = _position(trajectory)
        detail["magnitude"] = _float(trajectory.get("average_magnitude"))
        detail["report_count"] = _float(trajectory.get("nbre_total_reports"))

    durations, magnitudes = _witness_estimates(html)
    if detail["duration_s"] is None and durations:
        detail["duration_s"] = round(statistics.fmean(durations), 1)
    if detail["magnitude"] is None and magnitudes:
        detail["magnitude"] = round(statistics.fmean(magnitudes), 1)
    if detail["latitude"] is None:
        detail["latitude"], detail["longitude"] = _observer_centre(html)

    return detail


def _trajectory(html: str) -> dict[str, Any] | None:
    found = _TRAJECTORY.search(html)
    if not found:
        return None
    body = found.group("body")
    if body == "null":
        return None
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError:
        return None
    if isinstance(parsed, list) and parsed and isinstance(parsed[0], dict):
        return parsed[0]
    return None


def _position(trajectory: dict[str, Any]) -> tuple[float | None, float | None]:
    """The event's ground position, preferring the epicentre AMS computes."""
    for lat_key, lon_key in (
        ("epicenter_lat", "epicenter_long"),
        ("impact_lat", "impact_long"),
        ("end_lat", "end_long"),
        ("start_lat", "start_long"),
    ):
        latitude, longitude = _float(trajectory.get(lat_key)), _float(trajectory.get(lon_key))
        if latitude is not None and longitude is not None:
            return latitude, longitude
    return None, None


def _witness_estimates(html: str) -> tuple[list[float], list[float]]:
    """Durations in seconds and magnitudes, from the per-witness reports table."""
    _, tables = _read_tables(html)
    for table in tables:
        rows = _rows_as_dicts(table, _REPORT_FIELDS, _REPORT_REQUIRED)
        if not rows:
            continue
        durations = [d for d in (_seconds(row.get("duration")) for row in rows) if d is not None]
        magnitudes = [m for m in (_magnitude(row.get("magnitude")) for row in rows) if m is not None]
        if durations or magnitudes:
            return durations, magnitudes
    return [], []


def _observer_centre(html: str) -> tuple[float | None, float | None]:
    """Fall back to the mean witness position when no trajectory was computed."""
    found = _OBSERVERS.search(html)
    if not found:
        return None, None
    try:
        observers = json.loads(found.group("body"))
    except json.JSONDecodeError:
        return None, None

    latitudes: list[float] = []
    longitudes: list[float] = []
    entries = observers.values() if isinstance(observers, dict) else observers
    for entry in entries:
        for record in entry if isinstance(entry, list) else [entry]:
            if not isinstance(record, dict):
                continue
            latitude, longitude = _float(record.get("lat")), _float(record.get("lng"))
            if latitude is not None and longitude is not None:
                latitudes.append(latitude)
                longitudes.append(longitude)

    if not latitudes:
        return None, None
    return round(statistics.fmean(latitudes), 6), round(statistics.fmean(longitudes), 6)


def _apply_detail(event: Event, detail: dict[str, Any]) -> Event:
    """Fold detail-page values into an event built from the listing."""
    label = event.location.label if event.location else None
    latitude = detail.get("latitude")
    longitude = detail.get("longitude")
    location = (
        EventLocation(label=label, latitude=latitude, longitude=longitude)
        if (label or latitude is not None)
        else None
    )
    event.location = location
    event.magnitude = detail.get("magnitude")
    event.duration_s = detail.get("duration_s")
    reports = detail.get("report_count")
    if reports is not None:
        event.witness_count = int(reports)  # the detail page supersedes the listing's count
    return event


# ------------------------------------------------------------------------------------------
# listing rows to events
# ------------------------------------------------------------------------------------------


def rows_to_events(rows: Iterable[dict[str, str]], source_id: str) -> tuple[list[Event], list[str]]:
    events: list[Event] = []
    problems: list[str] = []
    for position, row in enumerate(rows):
        try:
            events.append(_event(row, source_id))
        except (KeyError, TypeError, ValueError) as exc:
            problems.append(f"row {position}: {exc}")
    return events, problems


def _event(row: dict[str, str], source_id: str) -> Event:
    reference, url = _identity(row)
    where = _where(row.get("states"), row.get("countries"))
    return Event(
        id=f"{source_id}-{reference}",
        source_id=source_id,
        source_ref=reference,
        kind=EventKind.FIREBALL,
        time=_parse_ut(row.get("time", "")),
        # The listing gives minute precision, averaged from eyewitness reports. Claiming the
        # second would be inventing precision the source never had.
        time_uncertainty_s=60.0,
        label=_label(where, row.get("reports")),
        location=EventLocation(label=where) if where else None,
        witness_count=_count(row.get("reports")),
        url=url,
    )


def _identity(row: dict[str, str]) -> tuple[str, str | None]:
    """Prefer the id in the row's link, which is unambiguous, over the displayed text."""
    href = row.get("href") or ""
    found = _EVENT_HREF.search(href)
    if found:
        return f"{found.group('number')}-{found.group('year')}", urllib.parse.urljoin(
            SITE_ROOT, href
        )
    text = (row.get("id") or "").replace("Event", "").strip()
    if not text:
        raise ValueError("no event id in the row")
    return text, None


def _drop_duplicates(events: list[Event]) -> list[Event]:
    """One event can be listed under several countries; keep the first sighting of each id."""
    seen: set[str] = set()
    unique: list[Event] = []
    for event in events:
        if event.id in seen:
            continue
        seen.add(event.id)
        unique.append(event)
    return unique


def _parse_ut(text: str) -> datetime:
    cleaned = _TRAILING_UT.sub("", (text or "").strip())
    if not cleaned:
        raise ValueError("no UT time in the row")
    for pattern in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(cleaned, pattern).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    raise ValueError(f"unreadable UT time {text!r}")


def _where(states: str | None, countries: str | None) -> str | None:
    """Read as a place: the regions, with the country codes in brackets after them."""
    states = (states or "").strip() or None
    countries = (countries or "").strip() or None
    if states and countries:
        return f"{states} ({countries})"
    return states or countries


def _label(where: str | None, reports: str | None) -> str:
    count = "".join(character for character in (reports or "") if character.isdigit())
    if where and count:
        return f"Fireball over {where} ({count} reports)"
    if where:
        return f"Fireball over {where}"
    return "Fireball"


# ------------------------------------------------------------------------------------------
# small value helpers
# ------------------------------------------------------------------------------------------


def _squash(parts: list[str]) -> str:
    return " ".join("".join(parts).split())


def _float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _seconds(text: str | None) -> float | None:
    """Read a witness duration such as ``≈7.5s``."""
    if not text:
        return None
    found = _SECONDS.search(text)
    return float(found.group("value")) if found else None


def _count(text: str | None) -> int | None:
    digits = "".join(character for character in (text or "") if character.isdigit())
    return int(digits) if digits else None


def _magnitude(text: str | None) -> float | None:
    if not text:
        return None
    found = _SIGNED_NUMBER.search(text)
    return float(found.group(0)) if found else None
