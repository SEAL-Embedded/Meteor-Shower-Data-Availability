> ## Status — superseded, kept for its domain notes
>
> **This brief commissioned the dashboard that now exists. Do not read it as a description of the
> repository.** It was written in August 2026, before the backend landed, and several of its
> statements were overtaken within days of being published:
>
> | The brief says | Actually |
> | --- | --- |
> | §0.1 Q2 "no scanner is in the repo today" | `backend/availability/ingest/` reads recording directories, SuperSID WAVs and CSV timeseries. It does **not** detect validation, loss, clock discipline or sensitivity — those remain human calls. |
> | §0.1 Q4 / §9 "blocking request: please commit `data/2024-campaign.json`" | Committed as `web/data/campaign.json` — 62 coverage intervals, 821 events, all measured. |
> | §4 diagnoses "the current website" (a Firebase page) | Deleted. The dashboard replacing it is `web/Data Availability Dashboard.dc.html`. |
> | §8 "proposed data model — not yet agreed" | Agreed and implemented; the contract is [`data-format.md`](data-format.md). |
> | §9 `3647f … −23` offered as the dispute test case | The scraped AMS record for that id reads 06:11 UT, −11, 7 reports. The record does hold five events at −26 or brighter, so the test case stands — that one does not. |
> | §12 "who converts the sheet's prose cells into JSON?" | Done; see `records/` and `tools/`. |
>
> **Still open**, and the reason this file is kept rather than deleted: §0.1 Q1 (who the primary
> user is), §0.1 Q3 (where manage-mode corrections go), and §2's rule that overlap must exclude
> undisciplined clocks — which the dashboard currently contradicts, because no record in the 2024
> season states clock discipline at all.
>
> What remains reliably useful here is the domain material, which is not written down anywhere else:
> §1 on why a VLF event is a perturbation rather than a ping, §2 on the five axes of availability,
> the confounds list, §9's hard cases, and the vocabulary appendix.

# Ionosphere Data Availability Dashboard — GUI Design Brief

**Project:** SEAL Embedded / Ionosphere Team, University of Washington
**Repo:** `SEAL-Embedded/Meteor-Shower-Data-Availability`
**Status:** Existing implementation being replaced. This brief kicks off the GUI design.
**Reader:** a designer with no prior knowledge of this project or of ionosphere science.

---

## 0. The one-paragraph version

We run a field campaign that points several instruments at the sky for weeks at a time to capture radio and optical signatures of meteors. Instruments stop and start unpredictably; files get corrupted; clocks drift; the sky clouds over. Before anyone can analyse the science, they need to answer a mundane but critical question: **what data do we actually have, when, and is it any good?** In 2024 we answered that with a Google Sheet that worked well. It was then rebuilt as a web page. That version got one important thing right — a continuously zoomable timeline instead of fixed hour buckets — and lost most of what made the sheet useful. We need a design for a dashboard that is genuinely better than the spreadsheet, not just prettier.

> **A note on this document.** It describes artefacts, never authors. Please keep it that way in any response.

---

## 0.1 Answer these before design starts

Four questions block the core of the work. Everything else in §12 can be resolved during design; these cannot.

1. **Who is the primary user?** Someone checking *"can I analyse last night's data?"* (favours second-precision zoom, gaps, and overlap) or someone reporting *"here is what the campaign yielded"* (favours aggregates and a matrix overview, and may not need continuous zoom at all)? These invert the priority list in §7. If the honest answer is "both equally," say so and we will budget for two entry points onto one data layer.
2. **Does the scanner script exist, and what can it actually detect?** No scanner is in the repo today. If it is not written, most fields in §8 are hand-entered indefinitely — which turns the management view from a small annotation surface into the primary data-entry tool and changes the design completely.
3. **Where do edits go?** The site is static with no backend (§10). If someone enters a meteor report or an analyst conclusion, does it persist to `localStorage` only, or produce a downloadable JSON the user commits by hand, or something else? And when the scanner regenerates the data file, are human-entered fields merged or overwritten? §11 deliverable 8 is unbuildable until this is answered.
4. **Is there real data to design against?** See §9 — the single most valuable thing the lab can provide before kickoff.

---

## 1. Background: what the project does

The Ionosphere team records natural and artificial disturbances in the upper atmosphere during meteor shower season.

**The physics, stated correctly**, because it drives the design:

> A meteoroid entering the atmosphere ablates at roughly 80–120 km altitude, and collisions between the ablated atoms and the air leave a narrow column of free electrons. That column can be seen optically, and it perturbs radio propagation — but not in one single way. At VHF (tens of MHz) a dense trail reflects radio waves directly. **Our radio instruments work at VLF (3–30 kHz), where the mechanism is different:** we monitor the amplitude and phase of distant fixed transmitters whose signals travel in the waveguide between the ground and the lower ionosphere, and a change in lower-ionosphere ionisation shows up as a change in that received signal.

**The design consequence:** a VLF "event" is a perturbation of a continuously present carrier, not a discrete ping. A coverage bar means *"we were watching a baseline"*, and quality means *"was the baseline clean"* — not *"did the receiver fire"*.

### Instruments

| Instrument | What it records | Notes |
|---|---|---|
| Sphere VLF Antenna System | VLF amplitude/phase | **Two channels** (ch0, ch1), validated separately |
| Vectaire Magnetometer | Magnetic field variation | **Three vector components** (X/Y/Z or H/D/Z) which can fail independently. The 2024 sheet's single validation column was a spreadsheet limitation, not an instrument property. Data are *relative*, not absolute field |
| Sky Camera | All-sky optical stills ("snaps") | Loss counted in snaps — see §8 on why that unit is dangerous |
| SuperSID | VLF signal strength, sound-card sampled | Normally a narrowband amplitude series per monitored transmitter, **not an audio recording — do not design a player.** Currently only start time and sample rate are reliably recorded; that is a gap to close, not a permanent constraint. Which transmitter it watches (e.g. NLK 24.8 kHz, NAA 24.0 kHz) is essential metadata — data without it is uninterpretable |
| NimbusTrace | Radio capture | Newer; details thin |

**The magnetometer's role** is characterising the geomagnetic background so a disturbance can be *excluded* as geomagnetic in origin. We are not claiming meteor detection in magnetometer data.

### Event sources

| Source | What it provides |
|---|---|
| American Meteor Society (AMS) | Crowd-sourced fireball reports: ID, time, region, duration, witness-estimated brightness |
| NASA All Sky Fireball Network | Paired-camera triangulated detections: trajectory, velocity, instrumental magnitude *(distinct from NASA CNEOS, which publishes radiated energy in joules, not magnitude — confirm which the 2024 sheet drew on)* |
| Sky Camera spot detections | Our own camera's detections |
| Launch records | Rocket launches. `eventVehicle` is free text, never an enum — the fleet turns over faster than this schema will |

### What else can cause a disturbance

Launches are **not** the main confound. In rough order of how often they bite:

- the day/night terminator crossing our propagation path
- lightning sferics from storms up to thousands of km away
- **solar flares** — the classic sudden ionospheric disturbance, literally what SuperSID hardware was built to detect
- geomagnetic activity
- scheduled outages of the VLF transmitters we monitor (looks like a catastrophic event)
- local RFI
- rocket launches — exhaust plumes deplete F-region electrons, ascent generates acoustic-gravity waves

**Co-occurrence with any of these does not disprove a meteor — it makes the attribution ambiguous. The design must show the ambiguity, not resolve it.**

> **The deepest scientific risk in this document.** Radio and optical detect largely *different meteor populations*: radio is sensitive to meteors far too faint to see, while AMS captures only the brightest few per night. Coincidence within a few minutes is therefore common **by chance**. This page reports what data exists; it must not be built in a way that invites eyeballing a coincidence and calling it a correlation.

### Time discipline

Everything is UTC, always, everywhere. No local time anywhere in the interface.

Timestamps are *stored* at second resolution — **this is a storage format, not an accuracy claim.** Real timing accuracy ranges from sub-second (our own camera frames) to ±minutes (witness-reported meteors) to worse (undisciplined instrument clocks). **The interface must never let storage resolution be mistaken for accuracy.** Note also that GPS time runs 18 s ahead of UTC, so the time scale has to be recorded rather than assumed.

### Campaign shape

The 2024 campaign ran continuously from **26 July to 9 September 2024** — about six weeks. It brackets the Perseid maximum (12 August 2024) but also covers the Southern δ-Aquariids and α-Capricornids (both ~30 July; the Capricornids produce slow, bright fireballs), the κ-Cygnids in mid-August, and the start of the Aurigids in early September. **Event density is therefore multi-modal across the campaign, and within every single night it rises steeply toward local dawn.**

The 2024 Perseid peak fell near a waxing gibbous Moon, which suppresses optical and crowd-sourced reporting — **a gap in the event record can mean fewer meteors, or fewer people who could see them.** The design should not imply that reported density equals actual activity.

*(Assumption worth confirming: that future campaigns have the same multi-week, multi-shower shape.)*

---

## 2. What "data availability" actually means here

The core concept, and where the previous attempt went wrong by treating it as binary. Availability is **at least five independent axes**:

1. **Was the instrument running?** Start and end. Sometimes still running; sometimes an end *derived* from sample count rather than observed.
2. **Did the files survive?** Present / partial / corrupt / missing — verified by checksum, by size only, or not at all, and the difference matters.
3. **Was the signal usable?** Distinct from 2. Files can be byte-perfect and the signal still garbage. Our 2024 vocabulary: `Valid` / `Invalid` / `Bad-looking`. **Per channel, or per vector component** — the antenna's ch0 can be valid while ch1 is not.
4. **Was the instrument *sensitive*?** Distinct from all of the above, and easy to miss. An all-sky camera running at noon or under overcast is up, its files are fine, and it saw nothing — roughly half of every 24 h of camera "coverage" is daylight. VLF sensitivity likewise differs fundamentally between day and night. **Coverage must be gated by an effective-sensitivity layer, or the page will show solid green for hours that yielded nothing.**
5. **Has it been processed and cleared for publication?** `draft` / `publishable` / `published`.

Plus a sixth, derived: **overlap** — intervals where multiple instruments were simultaneously up. This is the most scientifically valuable derived quantity on the page, because only overlapping windows support *cross-instrument* correlation. Non-overlapping coverage still matters for single-instrument studies and campaign-yield reporting and must not be visually demoted to background.

> **Overlap has a trap.** A free-running crystal clock drifts ~20–100 ppm — 2–9 seconds per day, accumulating monotonically. Over six weeks that is minutes of absolute error. Overlap computed by intersecting intervals whose absolute times are wrong by minutes is fiction, and it would be the page's headline number. Overlap must be computed over *usable* coverage (files intact **and** signal valid **and** clock disciplined), with the filter stated on screen and a minimum-duration threshold (a 4-second intersection is not an analysable window). Where a contributing interval is free-running, draw the overlap with a visible uncertainty margin at each edge and exclude it from headline totals. A total labelled "hours of three-instrument overlap" that silently includes undisciplined clocks is a *wrong* number, not an approximate one.

**Data loss is graded, not boolean** — the 2024 vocabulary, worth preserving:
- severity: `minor` / `major`
- recoverability: "Can be Recovered" is a **separate fact** from severity
- quantity: `(73 snaps)`, `(1 snap)`, `(around 36 snaps)`

---

## 3. Prior art: the 2024 Google Sheet (this is the real spec)

Titled *"Appendix: Big Sheet"*. **The lab considers this a success.** It is a high bar — the new GUI is not replacing something bad.

Structure: **one row per hour**, six weeks of rows, columns grouped into five categories:

| Category | Columns |
|---|---|
| Instrumentation Operational Hour Coverage | Sphere Antenna · Magnetometer · Sky Cam · **Operating Hours Overlap** |
| Ionosphere Activity Event Report | Skycam Spot Meteor Events · Falcon 9 Mission Record · NASA All Sky Fireball Camera · AMS |
| Instrumentation Functioning Validation | Sphere Ch 0 · Sphere Ch 1 · Magnetometer → `Valid`/`Invalid`/`Bad-looking` |
| Signal Processing Result | Result Archive URL · Signal Processing Conclusion |
| Publishable | `Publishable` / `Published` |

Plus a **reference-links row**: every column group links to the archive it came from. Provenance is built in.

It also has **per-instrument day × hour matrix tabs** — one grid per instrument, days down, 24 hours across, `S:` and `E:` markers in cells, giving whole-campaign coverage in one screen. *Whether that format is worth carrying forward is an open question in §12.*

### What the sheet does well (preserve these)
- **Everything for one hour is on one line** — coverage, events, validation, and processing state read together.
- **Multiple event sources stay in separate columns**, so corroboration between them is visible.
- **Notes live in place**, in plain language, at the exact hour they apply to.
- **Self-auditing.** One cell reads `← Error in sheet?` beside a suspicious value — a problem flagged without deleting the record. **The new design needs an equivalent.**

### Where the sheet genuinely hurts (the opportunities)
- **Hour granularity is a lie in both directions.** A 20-minute window and a 20-hour window look similar; an event precise to the second gets flattened into an hour bucket.
- **Everything is manual**, and one error is visibly flagged in the sheet itself.
- **Structured data trapped in prose.** `AMS ID: 3901d Time: 2024-08-02 05:45 UT Loc.: SC Dur.: ≈7.5s Magn.: -13` is a record stuffed into one cell — unsortable, unfilterable, uncountable.
- **No aggregate answers.** "How many hours of three-instrument overlap?" requires manual counting.
- **Merged cells everywhere**, which is what makes it fragile.
- **Not version-controlled and not owned by the project.** The new tool lives in the repo.

---

## 4. The current website, and precisely why it is confusing

One 1,351-line HTML file rendering an SVG timeline: continuously zoomable time (10 s to 10 y), a left column of four fixed lanes, drag-to-pan, a password-gated admin page, CSV/JSON import and export.

> **Its data does not live in a file.** The page loads the Firebase SDK from a CDN and reads/writes records to a hosted Firestore collection, with a `localStorage` fallback. **This was not asked for** — the requirement was and remains a JSON file committed to the repo. The redesign removes the backend entirely: no database, no sync, no connection state. This is the single most important thing not to reproduce.

**It is not all bad.** The zoom and pan mechanics are smooth and correct, and continuous zoom rather than fixed hour buckets is the right instinct — it directly fixes the sheet's granularity problem. Keep that.

**The specific diagnoses:**

1. **You lose your place while zooming.** At spans of roughly 1–4 days the axis reads `00:00 · 12:00 · 00:00 · 12:00` with no date — nine ticks, two distinct labels. There is no minimap, no context band, no "you are here" against the whole campaign. **Continuous zoom without an overview is the central usability failure.**
2. **Four fixed lanes, one of which is a category error.** Three instruments plus a "Meteor Events" lane. Events are not a measurement channel. Any instrument outside the hardcoded three is *silently not drawn* — SuperSID and NimbusTrace cannot appear at all.
3. **A bar carries no quality information.** Present or absent, one flat colour per instrument. Integrity, validation, loss, and sensitivity are all invisible. **This is the biggest regression from the sheet.**
4. **Event uncertainty is present but defeated by its defaults.** A dashed uncertainty band *is* drawn — but only when a value is supplied, and the code reads `rec.uncertaintyMin || 0`, so any event entered without one silently becomes exact, with a tooltip reading "reported to the second." Uncertainty is entered in whole minutes, so ±90 s cannot be expressed. And a sharp diamond plus a full-height dashed line are drawn at the point estimate regardless, so the precise mark always visually outranks the band. The fix is to make the interval the primary mark and the point estimate secondary.
5. **An unfinished recording is drawn as a 60-second bar.** A coverage record with no end gets a hardcoded one-minute duration, so an ongoing capture — possibly days long — appears as a blip.
6. **Aggregates answer the wrong questions.** There *is* a five-item legend, and there *is* a stat row (total records, coverage windows, meteor events) — but it lives in the admin view only, and it counts database rows, not science. Nothing reports instrument uptime, overlap hours, or per-instrument coverage percentage.
7. **A device panel displays fabricated status.** The "Antenna & Device Roster" shows `Sphere Channel 0: Operational`, `Validation: Active Sync`, `Feed: Live Remote Sync` — **hardcoded strings bound to no data whatsoever.** They will read as live status to any user. Nothing in the new design may display a status that is not derived from a record.
8. **Public and admin are disjoint modes**, so you cannot see the data while editing it.
9. **A record that cannot be drawn is silently skipped** — it still counts in totals and appears in the table, so the timeline and the numbers disagree with no warning.

Overall: the information architecture is legible; the orientation and the information density are not. That is what the redesign has to fix.

---

## 5. Reference points

- **NASA CNEOS Fireballs** — `https://cneos.jpl.nasa.gov/fireballs/` — previously cited by the team as directionally right: minute-level precision, sortable, CSV export. Note it is a *table*, not a timeline; worth understanding why that works and whether a table view belongs here too.
- The 2024 sheet's **day × hour matrix tabs** — the existing whole-campaign overview; see §12.

---

## 6. Non-goals

- Real-time streaming. Data updates when the scanner runs.
- Multi-user live editing, comments, accounts.
- Mobile-first.
- Analysis or signal processing. This page reports availability; it does not interpret data.

---

## 7. The questions the page must answer

Design against these, not a feature list. **The ordering is provisional** — it assumes an analyst asking "can I use last night's data?". If the answer to §0.1 Q1 is "campaign reporting," questions 6–7 move to the top and the information architecture shifts. Do not treat this order as fixed.

1. Which instruments were recording at time *T*, and was that data any good?
2. **When were multiple instruments simultaneously up and usable?**
3. Around a given event, what were we recording — and how confident are we about *when* it happened?
4. Where are the gaps, and why? (Down? Corrupt? Unusable? Daylight?)
5. Was there a plausible confound near this disturbance — **in time *and* in geometry**? A launch from Florida perturbing the ionosphere over the Atlantic may not touch our propagation path at all.
6. How much usable data did this campaign actually yield?
7. What is processed and cleared for publication, and what is outstanding?
8. Where is the raw data for this window?
9. Which records are disputed or need review?

---

## 8. Proposed data model — **not yet agreed**

The *shape* is settled: a static JSON file committed to the repo, fetched at load. **The field list is a proposal.** No scanner exists yet, so treat block (b) as aspirational.

**Do not design a unified table or a single lane type.** These record kinds do not share a schema and must not share a visual language: coverage records are *intervals of measurement*; event records are *reports of things that happened*; configuration records are *state changes*; interference records are *contamination periods*.

### Coverage record — (a) grounded in the 2024 sheet
```
instrumentId, channel        sphere_antenna/ch0 …  (extensible; unknown values must surface, never be dropped)
start, end                   RFC 3339 UTC, always "Z"
ongoing                      had not stopped at scan time
validation                   valid | invalid | bad_looking | unchecked
lossSeverity                 none | minor | major
lossRecoverable              true | false
lossQuantity, lossUnit       e.g. 73, "snaps"
lossDurationSec              REQUIRED alongside the above — see note
processingResultUrl, processingConclusion
publishState                 draft | publishable | published
disputed, disputeNote
referenceUrl, label, enteredBy
```

> **Why `lossDurationSec` is required.** "73 snaps" is uninterpretable without the camera cadence: at 1 fps that is 73 seconds, at 30 s cadence it is 36 minutes. Keep the native unit for provenance, but always carry seconds too, and display both: *"73 snaps (≈36 min at 30 s cadence)"*.

### Coverage record — (b) proposed, needs lab confirmation; **design so these degrade gracefully to absent**
```
status                       ok | partial | corrupt | missing | suspect | unknown
checkMethod                  sha256 | crc32 | size_only | parse_only | none
fileCount, expectedFileCount, filesFailed, byteCount
endBasis                     observed | derived_from_samples | derived_from_filesize | assumed_ongoing
endUncertaintySec            a derived end is a LOWER BOUND — never draw it as a hard edge
sampleRateHz, sampleCount
clockQuality                 disciplined | free_running | unsynced | unknown   ← separate from status
timeScale                    utc | gps | unknown
skyCondition, solarAltitudeDeg, moonAltitudeDeg      ← the sensitivity axis (§2.4)
provenance                   scanner | manual | reconciled
```

> **`clockQuality` is deliberately *not* inside `status`.** File integrity and clock discipline are orthogonal: a file can be byte-perfect and have untrustworthy timestamps. Folding them into one enum forces the analyst to discard one fact to record the other — the same category error diagnosed in §4.2.

### Event record
```
eventClass        meteor | fireball | launch | solar_flare | geomagnetic |
                  transmitter_outage | interference | other
eventSource       ams | nasa_allsky | skycam_spot | launch
start             best estimate, RFC 3339 UTC
eventEnd          launches have a "Mission End"
uncertaintyBasis  reported_precision | instrumental_fit | analyst_estimate | unknown   (REQUIRED)
uncertaintySec / uncertaintyLowSec / uncertaintyHighSec
                  may be null ONLY when basis is `unknown`, which renders as a distinct
                  "timing unknown" glyph — this avoids a silent zero without forcing
                  an analyst who genuinely does not know to invent a number
eventDurationSec, durationBinLowSec, durationBinHighSec
magnitudeValue, magnitudeBasis   witness_estimate | instrumental_photometry | energy_derived
eventLocation, eventVehicle, eventRefId, witnessCount
```

> **Duration bins are real information.** The `≈3.5 s` and `≈7.5 s` values throughout the 2024 data are AMS dropdown bin midpoints (3–4 s, 7–8 s). Store the bin, not a boolean "approximate" flag.

> **Magnitude is not one quantity.** A witness eyeball estimate, instrumental photometry, and an energy-derived figure are incommensurable. Keep the basis alongside the number.

### Proposed additional record kinds
- **Configuration record** — instrument state over an interval: gain, antenna azimuth, monitored transmitter (call sign + frequency), cadence, exposure, firmware. *Six weeks unattended guarantees a gain change or a re-aim, and an amplitude step at that boundary is an artefact, not a discovery.* A coverage bar spanning a config change should be visually segmented at that boundary.
- **Interference record** — a known contaminating interval: `rfi_local`, `powerline_harmonic`, `lightning_sferics`, `transmitter_maintenance`, `site_activity`.
- **Top-level `site` object** — lat/lon/altitude, camera field of view, VLF transmitter path endpoints. Question 7.5 is a geometry question and cannot be answered without this.
- **Top-level `dataset` block** — `datasetVersion`, `scanTimestamp`, `scannerVersion`. This page will be cited; a number quoted from it must be reproducible later.

**Scale:** hundreds to low thousands of records per campaign. Not a big-data problem — an information-density problem.

---

## 9. Data to design against

> **Blocking request to the lab.** Please commit `data/2024-campaign.json` — the full 2024 sheet mechanically converted, warts and blanks included, even if half the fields are null. **A lossy dump beats a curated sample.** If conversion is too much work, commit a raw CSV export of every tab and the designer can convert it in round one. Without this, every whole-campaign layout is built on invented density and will be wrong.

Until then, the cases below are the ones we consider hardest. **They are illustrative, not representative — note especially that none carries a real uncertainty value, which is the gap §0.1 and the request above exist to close.**

**A long, clean window** — `sphere_antenna`, 2024-08-01 00:20 → 2024-08-02 17:10 UTC (≈41 h)

**A heavily fragmented day** — Aug 6, sphere antenna: `… → 10:58` · `12:23 → 14:23` · `16:23 → 17:23`

**A very short window** — Aug 14: `01:00 → 01:20`. Must stay visible and distinguishable at campaign zoom.

**Partial loss inside a good window** — Jul 27 05:00–06:00, sky cam: *"Last half hour data lost"*, coverage then continues to `End: 06:13`.

**Graded loss** — Jul 29 10:03 `Major Data Lost`; elsewhere `Minor Data Lost / Can be Recovered`, `Minor data loss (73 snaps)`.

**The worked example — one hour, two candidate explanations:**
> **2024-08-02, hour 05:00 UTC**
> Launch: Falcon 9 Block 5, 05:01, Kennedy Space Center FL — "Mission End 08:19"
> Meteor: `AMS ID 3901d`, 05:45 UT, South Carolina, duration ≈7.5 s, magnitude −13
> Row marked: **Publishable**
>
> A disturbance in this hour has two candidate explanations, and the design must make that ambiguity visible rather than bury it.
>
> **But note:** "Mission End 08:19" is the *mission* timeline, not the confound window. The ionospherically relevant interval is roughly the first ~10 minutes (ascent plume, acoustic-gravity waves), plus a short separate window at any deorbit burn. **Drawing a 3 h 18 min red band would make every meteor in that stretch look confounded — scientifically wrong, and visibly discrediting.**

**Real AMS events** (note the binned durations):
```
3647f  2024-07-26 05:48 UT  MA  ≈3.5s  −23   ← see warning below
3646b  2024-07-27 00:50 UT  MA  ≈7.5s  −11
3901d  2024-08-02 05:45 UT  SC  ≈7.5s  −13
3908a  2024-08-02 09:04 UT  MO  ≈3.5s  −16
4676j  2024-08-27 05:20 UT  WI  ≈3.5s  −11
```
> **The −23 is a real record with an implausible value.** A genuine −23 is ~13,000× the full Moon — superbolide class, the kind that makes international news and drops meteorites. This is almost certainly witness over-estimation (AMS magnitudes are dropdown eyeball estimates and witnesses systematically overshoot). **This is exactly what the disputed / needs-review mechanism is for, and it is an excellent test case for the design.**

**Multiple sky-cam detections inside one hour:** `2024-07-29 03:45`, `03:49`, `03:53`

**A disputed record:** an `End: 15:25` sitting in the 03:00 row, annotated `← Error in sheet?`

---

## 10. Requirements and constraints

**Must have**
- Continuous zoom from whole-campaign to **second precision**, with the user never losing orientation.
- **Public read-only view** and a **management mode**. The management mode is *not* a security boundary — the page is static and world-readable. **Do not design a password screen.**
- **Distinct visual treatment for coverage vs. events.** Events must never be drawn as an exact instant; timing uncertainty must be legible in the mark itself.
- **Quality encoded in the mark** — status, validation, loss, and sensitivity visible without hovering.
- **Overlap made visible**, computed over *usable* coverage with the filter shown on screen (§2).
- **Daylight and twilight visually distinct from dark-sky** in every optical lane, at every zoom level.
- Every timestamp unambiguously UTC; storage resolution never presented as accuracy.
- Dataset version and scan timestamp visible on every view and in every export.
- If any record cannot be displayed, **say so visibly**. Never silently drop data. Never display a status not derived from a record.
- Export to CSV and JSON.
- A legend for every encoding used.

**Nice to have**
- Distinguishing "we have a ± " from "we only know it was in this window." See §12 — we may not have the data to support it.

**Constraints**
- **Static site on GitHub Pages. No server, no database, no login, no third-party backend service.** Data is a JSON file in the repo, fetched at load. **This is a hard requirement, not a preference** — the current implementation uses a hosted database and that is the primary reason it is being replaced.
- **Self-contained: no external CDN dependencies.** The current page loads fonts, a CSV parser, and the Firebase SDK from four external hosts; none of that survives.
- Desktop-first (a lab tool), must not break on a tablet.
- Light and dark.
- Accessible: keyboard-operable timeline, and **never colour alone** to carry status — shape, texture, or label must also encode it.

---

## 11. What we would like back

1. **Information architecture first** — what views exist and how they relate. One timeline? Timeline plus overview matrix plus table? Make the case.
2. **The orientation solution.** How does someone zoom to second precision without getting lost? **Highest-value problem in this brief.**
3. **A visual language for quality** — status, validation, loss severity, recoverability, and sensitivity at a glance, surviving greyscale.
4. **The event uncertainty treatment** — how *"we think it was 05:45, ±90 s, and honestly nobody knows"* looks. Propose the **minimum** number of distinct treatments that do the job. Note these bands span three orders of magnitude: our own skycam detections are certain to the frame, AMS reports to ±minutes. At 12-hour zoom an AMS band is invisible; at ±5 min zoom it fills the screen. The treatment must stay legible across that range.
5. **Layouts at three zoom levels:** whole campaign (6 weeks), one night (12 h), one event (±5 min).
6. **The dense case**, using the 2024-08-02 05:00 hour.
7. **Empty, sparse, and error states.**
8. **The management mode** — how it relates to the public view, for these specific tasks: add an event, annotate a coverage record, set publish state, mark disputed, write a conclusion. *(Blocked on §0.1 Q3.)*

Static mockups are fine to start. **An interactive HTML prototype of the timeline interaction would be more useful than pixel-perfect comps**, because orientation is a motion problem.

---

## 12. Open questions for the lab

*(The four blocking ones are in §0.1.)*

- Should the public view default to `published` records only?
- **Who is the public view for** — the lab, named collaborators, or genuinely anyone? The plan assumes world-readable. If unreviewed coverage or dispute records should not be public, that changes what the JSON file is allowed to *contain*, not just what the page renders.
- Which fields are scanner-generated and which are human judgement? Our reading: validation, loss recoverability, dispute flags, processing conclusions, and publish state are all human calls — which makes the management view a substantial editing surface for coverage records, not just a place to type meteor reports.
- For AMS and NASA events, can we characterise timing uncertainty beyond a single ±, or is one interval treatment all the data will ever support?
- How many instruments at peak — 5, or 15? Decides whether lanes scale.
- Is the day × hour matrix actively wanted, or a workaround for the spreadsheet medium?
- Is the 2024 campaign loaded as historical data, or does the page only show the current campaign? What data exists right now besides 2024? Who converts the sheet's prose cells into JSON?
- **Do people print this into a lab notebook?** If yes, greyscale legibility is a hard constraint; if no, we have more freedom with colour.
- What is the peak event density on a busy night? This single number decides whether the whole-campaign view is a timeline or a heatmap.

---

## Appendix: vocabulary

| Term | Meaning |
|---|---|
| Coverage / capture window | A continuous interval during which an instrument was recording |
| Overlap | Interval where 2+ instruments were simultaneously recording *and usable* |
| Snap | One still image from the sky camera; unit of sky-cam data loss |
| VLF | Very low frequency radio, 3–30 kHz |
| Sferic | Broadband radio impulse from a lightning stroke; dominates the VLF band |
| SID | Sudden Ionospheric Disturbance — D-region ionisation change, classically from a solar flare |
| Fireball | A meteor brighter than magnitude −4 (roughly Venus), per the IAU/AMS working definition |
| Magnitude | Logarithmic brightness; more negative is brighter, 5 magnitudes = ×100. Venus ≈ −4, full Moon ≈ −12.7, Sun ≈ −26.7 |
| Bad-looking | 2024 term: files intact, signal visibly unusable |
| Ongoing | Recording had not stopped when the scanner ran |
| Derived end | End time computed from sample count, not observed — a *lower bound*, not a hard edge |
| Clock disciplined | Timestamps steered by GPS or NTP; otherwise drift accumulates at 2–9 s/day |
