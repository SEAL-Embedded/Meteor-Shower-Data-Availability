# Meteor Shower Data Availability

A public record of **when our ground-based instruments were recording**, and **what happened in the
sky while they were.**

We operate very low frequency (VLF) radio receivers that listen for transient ionospheric
disturbances — the signature that a meteor, a rocket launch, or a solar flare leaves behind as it
perturbs the lower ionosphere. A recording is only scientifically useful if you know, precisely, that
the instrument was running when the event occurred. This project publishes that record.

The site answers one question directly:

> A fireball was reported at 05:45 UTC. Was anything of ours actually recording?

## What it shows

- **Coverage timelines.** For every instrument, the exact UTC windows it was recording, with a
  quality flag distinguishing clean data from degraded or lost stretches.
- **Overlap windows.** The intervals during which two or more instruments were recording
  simultaneously — the periods that support cross-instrument corroboration.
- **Events.** Meteor and fireball reports drawn from public catalogues, placed on the same timeline.
- **A coverage verdict per event.** For each event: covered, partially covered, not covered, or
  unknown. "Not covered" means we know we were down. "Unknown" means we have not yet characterised
  that period — the two are never conflated.

Everything is in UTC. There is no local-time display anywhere in the data model.

## Instruments

| Instrument | Type | Role |
| --- | --- | --- |
| NimbusTrace | VLF receiver, 0–50 kHz | Primary instrument. Autonomous, solar-powered field node. |
| SuperSID | VLF receiver | Second, independent receive chain, locally operated. Corroborates NimbusTrace. |
| Sky camera | Optical | Planned. Optical confirmation of events seen in the radio band. |
| Magnetometer | Geomagnetic | Planned. |

Instruments are defined in configuration, not in code — adding a fourth is a config entry plus an
ingest adapter.

## Event sources

Events are drawn from public catalogues and are **attributed, never claimed as our own
observations**. Each event carries its source, the source's own identifier, and a link back to the
original record.

Fireball events come from the **American Meteor Society**. Each event keeps that society's own
identifier and links back to its page there. Events are read in two stages: a listing that gives the
time and place, then each event's own page for magnitude, duration and computed position. Fetched
pages are cached and re-read rather than re-requested, requests are spaced out, and a single run is
capped so a backlog is worked through over several runs rather than in one long crawl.

Events are restricted to those **within range of the instrument** — a configurable radius around it,
5000 km by default, which covers North America. An event whose position cannot be determined is kept
rather than dropped, because a missing coordinate is not evidence of distance. Every kept event
stores its coordinates, so a different geometry can be applied later without re-fetching anything.

Two properties of the source are worth stating plainly. Times are reported **to the minute**, so
every event carries a one-minute timing uncertainty that widens its coverage window rather than
pretending to second precision. And magnitude and duration are **averages of eyewitness estimates**,
not instrument measurements — useful for ranking events, not for physics.

NASA's All Sky Fireball Network and public orbital launch schedules are planned. Both are gated on
confirming access terms, and until then their adapters report why they are switched off rather than
returning anything.

## How it works

The backend is a Python service that runs on the machine where the instrument data physically lives.
It has two modes, and they share all of their logic:

```
  instrument files                    ┌──────────────────────┐
  on the data workstation ──────────► │                      │
                                      │  ingest → intervals  │
  public event catalogues ──────────► │  → overlap → verdict │
                                      │                      │
                                      └──────┬────────┬──────┘
                                             │        │
                    publish (scheduled)      │        │   serve (on demand)
                    static JSON snapshots ◄──┘        └──► live HTTP API
                             │                                    │
                             ▼                                    ▼
                    ┌────────────────────────────────────────────────┐
                    │   static web front end (GitHub Pages)          │
                    │   reads the API when reachable,                │
                    │   falls back to the published snapshot         │
                    └────────────────────────────────────────────────┘
```

**Publish mode** writes versioned JSON snapshots into `web/data/`, which are committed and deployed
as a static site. The site therefore stays available and correct even when the data workstation is
offline; it simply shows an older "generated at" timestamp.

**Serve mode** exposes the same data over an HTTP API for live status. The front end tries the API
first and silently falls back to the last published snapshot, so a workstation reboot degrades the
page rather than breaking it.

Both modes read the identical snapshot structure, documented in
[`docs/data-format.md`](docs/data-format.md). The front end is written against that contract and
against nothing else.

## Repository layout

```
backend/            Python service — ingest, interval algebra, API, publisher
  availability/
    ingest/         One adapter per data source; add a source without touching core logic
    core/           Interval algebra, event/coverage correlation, snapshot assembly
  tests/
web/                Everything published to GitHub Pages
  index.html        Landing page; forwards to the dashboard
  Data Availability Dashboard.dc.html   The dashboard — timeline, campaign, records, manage
  campaign-data.js  Loads the generated dataset; falls back to a synthetic one if it is absent
  support.js, _ds/  The dashboard's runtime and design system
  reference.html    A minimal renderer built against docs/data-format.md, kept as a worked example
  data/             Generated JSON: the contract snapshots and the dashboard's dataset
docs/               Data format contract, operator notes, CSV templates
records/            The transcribed 2024 season coverage
tools/              Extraction scripts, so the transcription is reproducible
```

Operational detail — scheduled publishing, serving live, adding a source — is in
[`docs/operating.md`](docs/operating.md).

## Quick start

Nothing needs installing. Python 3.11 or newer, and a clone.

Copy the example configuration and point it at your data directories:

```bash
cp config.example.toml config.toml
```

See what every configured source finds, without writing anything:

```bash
python run.py check --config config.toml
```

Generate the published data:

```bash
python run.py publish --config config.toml --strict
```

View the site:

```bash
python -m http.server 8080 --directory web
```

Run the tests:

```bash
python -m pytest backend/tests
```

`--config` may be given before or after the command. Installing the package
(`python -m pip install -e "backend[dev]"`) is optional and gives you an `availability` command that
does the same as `python run.py`; Flask is only needed for `serve`.

## The front end

`web/` is served as-is: no build step, no framework toolchain, no bundler.

The dashboard is the interface — four views over one record set, with the timeline for "can I use
last night" and the campaign view for "what did we yield". It reads `web/data/campaign.json`,
which `publish` generates, and falls back to a synthetic dataset if that file is missing. It says
which one it used; a synthetic set that looks real is the worse failure.

`web/reference.html` is a much smaller renderer written directly against
[`docs/data-format.md`](docs/data-format.md). It is not the site — it is a worked example of the
documented contract, useful to anyone building a second consumer.

If you change the shape of the published JSON, bump `schema_version` and update the contract document
in the same change.

## Status

Under active development.

**Working and covered by tests:** the interval algebra and overlap computation, event correlation
including the covered/not-covered/unknown distinction, snapshot publishing, the HTTP API, the
recording-directory scanner, and CSV and JSON ingest for hand-maintained records.

**Working, with a standing caveat:** the American Meteor Society adapter. Its parsing is tested
against markup captured from the live table, and it locates columns by their headers rather than by
position, so a reordering upstream is followed rather than misread. It is still scraping: if that
page is redesigned it will stop working, and it is built to say so — unrecognised headers produce a
reported failure, never a partial import.

**Working, not yet validated against real data:** the SuperSID daily-log reader. Run
`python run.py check` against a period someone can verify by hand before publishing a record
built from it.

**Not implemented:** the NASA All Sky Fireball Network and orbital launch adapters. Both are
registered and report why they are switched off; neither fetches anything. Until their access terms
are settled, import an export you obtained yourself through the CSV or JSON event adapter.

## License

MIT — see [LICENSE](LICENSE).
