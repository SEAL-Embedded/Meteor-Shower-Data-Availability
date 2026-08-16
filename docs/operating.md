# Operating the service

Two things run on the machine that holds the instrument data: a scheduled **publish** that keeps the
static site current, and an optional **serve** that answers live queries. The published snapshot is
the floor — it is what the site falls back to — and the live API is the enhancement on top.

## Publishing on a schedule

`publish` reads every configured source and writes JSON into `web/data/`. Committing and pushing that
output is what updates the public site.

```bash
python run.py publish --config config.toml
```

Wrap it with the commit and push:

```bash
python run.py publish --config config.toml --strict \
  && git add web/data \
  && git commit -m "Update availability record" \
  && git push
```

`--strict` refuses to publish when any source errored, so a mount that dropped overnight does not
quietly shorten the record. Without it, a failed source is published as a failed source — visible on
the page, with the other sources intact. Which you want depends on whether an incomplete record is
worse than a stale one; for a public availability record, usually it is.

On Windows, run it from Task Scheduler with the action `python run.py publish ...` and the
working directory set to the repository. Schedule it well after the instruments roll over their
files for the day, so a partly written recording is not read as a short one.

## Serving live

```bash
python run.py serve --config config.toml --host 127.0.0.1 --port 8000
```

Flask's development server is fine behind a tunnel for a read-only API at this scale. Two things must
be true before the published front end can call it:

1. **The endpoint must be HTTPS.** The site is served over HTTPS, and a browser will not let an
   HTTPS page call a plain `http://` API. A tunnel that terminates TLS for you — Cloudflare Tunnel,
   Tailscale Funnel — is the simplest way there, and avoids forwarding a port or exposing the
   machine's address.
2. **The origin must be allowed.** List the site's origin in `[api] allowed_origins` in
   `config.toml`. Nothing is allowed by default, so the API answers no cross-origin browser request
   until you name one.

Then set `AVAILABILITY_API_BASE` in `web/config.js` to the tunnel's HTTPS origin and commit it. The
front end tries the API, waits `AVAILABILITY_API_TIMEOUT_MS`, and falls back to the published
snapshot if it does not answer — so taking the workstation down degrades the page to its last
snapshot rather than breaking it.

The API rebuilds its view of the record at most once every `--refresh-seconds` (60 by default).
Ingest walks directories and reads files, so serving that per request would make cost scale with
traffic instead of with data.

## Checking a source before trusting it

```bash
python run.py check --config config.toml
```

`check` runs every source, prints what each found, and writes nothing. Use it after changing any
path or adding an instrument. It exits non-zero if a source failed, which makes it usable as a
pre-publish gate.

Two things it is specifically good for:

- **After pointing `file_scan` at a new directory.** Confirm the interval count and the characterised
  range match a period you know by hand. A wrong `timestamp_format` typically shows up as far too
  few intervals, or a range in the wrong century.
- **The first time the SuperSID adapter runs.** Its header parsing follows the standard log layout
  but has not yet been validated against this installation's output. Compare a day you can verify
  before publishing anything built from it.

## Fireball events from the American Meteor Society

Two stages, costing very different amounts.

**The listing** is one request per fifty events. It carries the event id, report count, UT time to
the minute, countries and states — but no magnitude, duration or coordinates.

**Each event's own page** carries all three, and is one request per event. That is where the cost
lives, and `countries` is what bounds it:

| Scope, 2024 | Listed events | Detail requests |
| --- | --- | --- |
| Worldwide | ~20,000 | ~20,000 |
| US + Canada + Mexico | ~5,300 | ~5,300 |
| US only | ~4,750 | ~4,750 |

Raising `min_reports` looks like the obvious economy and is a trap. The site defaults to 5, but the
fireballs logged in the 2024 season had as few as **2** reports; at `min_reports = 10` five of the six
would vanish. Leave it at 1.

So the backlog is worked through instead of avoided. `max_detail_fetches` caps a single run at 500 by
default; cached pages are free and don't count, so a nightly job fills in a few hundred more each
time and converges over a couple of weeks. Once an event is older than `detail_settle_days` its page
is cached indefinitely, because a past fireball's trajectory does not change. Every run reports how
many events are still awaiting detail — a capped run is never silently mistaken for a complete one.

If you would rather do it in one sitting, set `max_detail_fetches` high and expect a few hours at
`delay_s = 2`. That is a lot of requests to a volunteer-run site; the incremental default exists for
a reason.

### The distance filter

`origin_lat`, `origin_lon` and `max_distance_km` keep only events within reach of the instrument.
Two things to understand about it:

- **It needs coordinates, which only exist on the detail page.** So it runs *after* the fetch and
  trims the result; it does not reduce the request count. `countries` is the lever for that.
- **An event whose position cannot be determined is kept, not dropped**, and counted separately in
  the source detail. A missing coordinate is not evidence of distance.

5000 km from Seattle covers North America, Hawaii and Mexico. 3000 km would have excluded four of the
six events in the 2024 record, all of them in the eastern United States. Every kept event stores its
coordinates, so a different geometry — proximity to a transmitter-to-receiver path rather than a
radius around the receiver — can be applied later without re-fetching anything.

### When it breaks

It is scraping, so it will. Columns are located by **header text** rather than position, so a
reordered table is followed correctly; and when the headers match nothing known, the source reports
`error` with the headers it actually found and imports nothing. A partial import with columns shifted
by one would be far worse than a visible failure.

`python run.py check` names the headers it saw, which is usually enough to fix the mapping
in one edit. Delete the relevant files under `.cache/ams` first, or you will re-read the old copy.

AMS also publishes a documented REST API at <https://www.amsmeteors.org/members/imo_api/>, needing a
key they issue to paid members and invited scientific organisations. It is the sturdier route if this
ever becomes more trouble than it is worth.

## Adding a data source

1. Write an adapter in `backend/availability/ingest/`, subclassing `Adapter` and decorated with
   `@register("name")`. Return a `CoverageResult` or an `EventResult`.
2. Import it in `backend/availability/ingest/__init__.py`.
3. Add a `[[sources]]` block naming that adapter.

Adapters report failure rather than raising: a missing directory or an unreachable catalogue sets
`status` and `detail` on the returned source and yields no records. One broken source must not take
the whole published record offline.

Set `known_ranges` on a `CoverageResult` to the period the adapter actually examined, which is not the
same as the period it found data in. That distinction is what separates "we were down" from "we never
looked", and it is the only place the difference can be established.
