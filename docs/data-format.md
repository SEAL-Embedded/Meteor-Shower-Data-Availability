# Data format contract

**Schema version: `1.0`**

This document defines the JSON the front end consumes. The static snapshots in `web/data/` and the
live HTTP API return the identical structures — a client that reads one reads the other.

All timestamps are ISO 8601 in UTC with a `Z` suffix and second precision: `2024-07-27T00:50:00Z`.
There are no local-time fields anywhere. Durations are in seconds. Frequencies are in hertz.

## Endpoints and files

| Static file | Live endpoint | Contents |
| --- | --- | --- |
| `data/index.json` | `GET /api/v1/index` | Manifest: schema version, generation time, instruments, sources, available years |
| `data/<year>.json` | `GET /api/v1/years/<year>` | All coverage, segments, events and verdicts for one UTC calendar year |
| — | `GET /api/v1/coverage?start=&end=` | Coverage intervals in an arbitrary range |
| — | `GET /api/v1/events?start=&end=&source=&kind=` | Events with verdicts in an arbitrary range |
| — | `GET /api/v1/status` | What is recording right now |
| — | `GET /api/v1/health` | Liveness probe |

Clients should attempt the live API and fall back to the static files. A client that only reads the
static files is fully functional apart from live status.

### Query parameters

`start` and `end` are ISO 8601 timestamps. Omitted, `coverage` defaults to the characterised period
and `events` defaults to a window wide enough to include every event held — including events outside
any instrument's `known_range`, which are exactly the ones carrying an `unknown` verdict.

`source` and `kind` filter events, and both accept a comma-separated list:

```
GET /api/v1/events?source=ams&kind=fireball
GET /api/v1/events?kind=fireball,rocket_launch&start=2024-08-01T00:00:00Z
```

`event_coverage` is filtered alongside `events`, so the two arrays always correspond. An
unrecognised `source` or `kind` returns **400** naming the valid values rather than an empty list —
an empty list is indistinguishable from a quiet period, and a typo should not look like one.

**No endpoint fetches from an external catalogue on request.** External sources are read when the
record is built, behind their own caches and request budgets. Serving a page must never become a
request to somebody else's site.

## `index.json`

```json
{
  "schema_version": "1.0",
  "generated_at": "2026-08-15T18:00:00Z",
  "range": { "start": "2024-07-26T00:00:00Z", "end": "2024-09-09T00:00:00Z" },
  "years": [2024],
  "instruments": [ /* Instrument */ ],
  "sources": [ /* Source */ ]
}
```

`range` is the union of every instrument's known range. `years` lists the year files that exist.

## Instrument

```json
{
  "id": "nimbustrace-seattle",
  "name": "NimbusTrace VLF Receiver — Seattle",
  "kind": "vlf",
  "system": "nimbustrace",
  "site": {
    "id": "seattle",
    "name": "Seattle, Washington",
    "latitude": 47.6553,
    "longitude": -122.3035
  },
  "band_hz": { "low": 0, "high": 50000 },
  "active": true,
  "known_range": { "start": "2024-07-26T00:00:00Z", "end": "2024-09-09T00:00:00Z" }
}
```

`kind` is one of `vlf`, `magnetometer`, `sky_camera`, `other`.

`known_range` is the period over which this instrument's availability has actually been
characterised. **Outside it, absence of coverage means "not yet determined", not "was not
recording".** Clients must not render a gap outside `known_range` as downtime. `site`, `band_hz` and
`known_range` may each be `null`.

## Source

```json
{
  "id": "ams",
  "name": "American Meteor Society",
  "kind": "event",
  "url": "https://www.amsmeteors.org/",
  "attribution": "Fireball reports courtesy of the American Meteor Society",
  "fetched_at": "2026-08-15T17:58:00Z",
  "status": "ok",
  "detail": null
}
```

`kind` is `coverage` or `event`. `status` is `ok`, `stale`, `error` or `disabled`; when it is not
`ok`, `detail` carries a human-readable reason. A source in error does **not** remove its previously
ingested records — it marks them stale so the page can say so.

## `<year>.json`

```json
{
  "schema_version": "1.0",
  "generated_at": "2026-08-15T18:00:00Z",
  "year": 2024,
  "coverage": [ /* CoverageInterval */ ],
  "segments": [ /* Segment */ ],
  "events": [ /* Event */ ],
  "event_coverage": [ /* EventCoverage */ ]
}
```

Records that straddle a year boundary appear in both year files, clipped to that year. Coverage is
sorted by `start`, then `instrument_id`.

## CoverageInterval

```json
{
  "instrument_id": "nimbustrace-seattle",
  "start": "2024-07-26T23:40:00Z",
  "end": "2024-07-27T18:51:00Z",
  "quality": "good",
  "note": null,
  "source_id": "nimbustrace-files"
}
```

`quality` is one of:

| Value | Meaning |
| --- | --- |
| `good` | Complete, usable data for the whole interval. |
| `degraded` | Data present but incomplete or impaired; may be partially recoverable. `note` should say how. |
| `lost` | The instrument was nominally running but the data for this interval is not usable. |

A `lost` interval is deliberately *not* the same as no interval at all. It records a known failure
rather than an unexamined period.

## Segment

A segment is a derived, half-open interval `[start, end)` during which a fixed set of instruments was
recording. Segments are produced by a sweep over all coverage intervals, are non-overlapping, and are
sorted by `start`. Adjacent segments always differ in either their instrument set or their
`min_quality`, so a mid-window drop from clean to impaired data stays visible rather than being
absorbed into its neighbour.

```json
{
  "start": "2024-07-27T03:14:00Z",
  "end": "2024-07-27T06:13:00Z",
  "instrument_ids": ["nimbustrace-seattle", "supersid-seattle"],
  "degree": 2,
  "min_quality": "good"
}
```

`degree` is the number of instruments recording, and is the field to filter on for overlap views:
`degree >= 2` gives every corroborated window. `min_quality` is the worst quality among the
contributing intervals — a segment is only as trustworthy as its weakest instrument.

Segments with `degree` of zero are not emitted.

## Event

```json
{
  "id": "ams-3646b",
  "source_id": "ams",
  "source_ref": "3646b",
  "kind": "fireball",
  "time": "2024-07-27T00:50:00Z",
  "end_time": null,
  "time_uncertainty_s": 60,
  "label": "Fireball over Massachusetts",
  "location": { "label": "Massachusetts, United States", "latitude": null, "longitude": null },
  "magnitude": -11.0,
  "duration_s": 7.5,
  "witness_count": 9,
  "url": "https://www.amsmeteors.org/members/imo_view/event/2024/3646"
}
```

`witness_count` is how many independent reports the source holds. It matters because `magnitude` and
`duration_s` are averages of eyewitness estimates: one averaged from two reports and one averaged
from forty are not equally well attested, and nothing else in the record says so.

`kind` is one of `fireball`, `meteor_shower`, `rocket_launch`, `other`. `end_time` is set for events
with real duration, such as a launch window; for point events it is `null` and the event is treated
as an instant. `time_uncertainty_s`, when present, widens the event to `[time - u, time + u]` for the
purpose of computing coverage.

Every field except `id`, `source_id`, `kind` and `time` may be `null`.

## EventCoverage

```json
{
  "event_id": "ams-3646b",
  "verdict": "covered",
  "degree": 2,
  "covering": [
    { "instrument_id": "nimbustrace-seattle", "quality": "good" },
    { "instrument_id": "supersid-seattle", "quality": "good" }
  ]
}
```

| Verdict | Meaning |
| --- | --- |
| `covered` | At least one instrument was recording at `good` quality for the event's whole window. |
| `partial` | Instruments were recording, but only at `degraded`/`lost` quality, or only for part of the window. |
| `not_covered` | The event falls inside at least one instrument's `known_range`, and nothing was recording. |
| `unknown` | The event falls outside every instrument's `known_range`. We have not characterised that period. |

`covered` and `not_covered` are claims. `unknown` is an admission. Clients must render them
distinctly; collapsing `unknown` into `not_covered` misreports our record.

## `status` (live API only)

```json
{
  "as_of": "2026-08-15T18:04:11Z",
  "recording": [
    { "instrument_id": "nimbustrace-seattle", "quality": "good", "since": "2026-08-15T04:12:00Z" }
  ],
  "degree": 1
}
```

## Errors

Live API errors are JSON, with the HTTP status carrying the meaning:

```json
{ "error": "not_found", "message": "No data for year 2019" }
```

## The dashboard dataset

The dashboard in `Data Availability Dashboard.dc.html` reads a second format of its own design —
epoch-millisecond timestamps, its own field names and controlled vocabularies. `publish` writes it to
`data/2024-campaign.json` when `[campaign] enabled = true`. It is a translation of the same record,
not a second source of truth.

The mapping is configured, not hardcoded, so the front end can rename things without the record
having to:

```toml
[campaign.instrument_ids]
"sphere-vlf-seattle" = "sphere_antenna"

[campaign.provenance]
"record-2024" = "sheet_2024"
"ams" = "ams_scrape"
```

Two rules hold in that translation, and both exist to stop a value being invented in transit:

- **Only fields we hold are emitted.** The dashboard applies its own defaults to whatever is absent,
  so a missing field reads as "not determined" rather than as a value we made up.
- **A human check is still a check.** Coverage transcribed from a season sheet reports as `valid`
  (or `invalid` where the sheet records major loss), with `checkMethod` of `operator_log` rather than
  one of the scanner's methods. An earlier version reported `unchecked` on the grounds that no
  scanner had run; because the dashboard draws unchecked coverage as a hollow outline, that made a
  fully characterised season look like one nobody had examined.

`campaign` is the period the instruments were characterised over, not the period events are known
for — a year of catalogue events must not stretch a 45-night observing run across the whole calendar.
Events outside it are still included.

### `jumps`

Notable windows in the record, for the dashboard's jump bar. Optional.

```json
"jumps": [
  { "id": "campaign",     "label": "Whole campaign",  "start": 1721959320000, "end": 1725844500000, "detail": "45 days" },
  { "id": "largest-gap",  "label": "Largest gap",     "start": 1721959320000, "end": 1722063000000, "detail": "21 h with nothing recording" }
]
```

`id` is stable and machine-readable; `label` is what the button says; `detail` states the measured
fact behind it, which the dashboard shows on hover. `start` and `end` are framed a little wider than
the thing they point at, so it has context around it, and are always inside `campaign`.

These are **computed when the record is published, not by the client.** Two reasons, and the second
is why it is worth a section of its own:

- A figure quoted from this page — "the longest gap in the 2024 season was 21 hours" — should be
  reproducible from the dataset later, not recomputed differently by whichever client read it.
- They were previously literal timestamps written into the front end against a sample dataset. Once
  the measured record replaced that sample, the button labelled *Gap* pointed into the middle of an
  unbroken four-day run: a control asserting we were down, sitting on the most continuous stretch of
  the season. That is exactly the confusion the `covered` / `not_covered` / `unknown` distinction
  exists to prevent, and it is not a mistake a client-side default can be trusted not to repeat.

**An entry appears only where the record holds one.** A campaign with no gap publishes no
`largest-gap`; a record with no events publishes none of the event-density entries. This is the same
rule as everywhere else in the export — an absent field means *not determined*, never a claim — and
here it has teeth: the dashboard builds its bar from whatever is present, so it cannot offer a button
promising a window the record does not contain. A dataset carrying no `jumps` at all gets no bar.

The ids currently emitted are `campaign`, `busiest-night`, `dense-hour`, `closest-look`,
`shortest-run`, `most-fragmented` and `largest-gap`. Clients must not depend on that list being
complete or ordered; read what is there.

This is additive, and only to the dashboard dataset — the contract snapshots above are unchanged, so
`schema_version` stays at 1.0. A client that does not recognise `jumps` ignores it and is otherwise
unaffected.

## Compatibility

`schema_version` follows major.minor. A minor bump only adds fields; clients must ignore fields they
do not recognise. A major bump may remove or repurpose fields, and published snapshots of the
previous major version remain in place until every client has moved.
