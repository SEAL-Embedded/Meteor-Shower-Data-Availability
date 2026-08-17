# Handover — dashboard now runs on real data

**Please `git pull` and read this before you touch anything.** I've changed code inside your files,
including the design component, and moved the whole bundle into a different folder.

---

## 1. The dataset is real now

`data/2024-campaign.json` exists — the thing your `campaign-data.js` comment asked for. It's
generated from the lab record by the Python service in `backend/`, and the synthetic generator is
still there as a fallback.

| | Was | Now |
| --- | --- | --- |
| Provenance | `synthetic` | `measured` |
| Coverage | invented density | **62 real intervals**, three instruments |
| Events | invented | **821 AMS fireballs**, all within the season |
| Site | badged ASSUMED | real coordinates, `assumed: false` |
| Campaign | 26 Jul – 9 Sep 2024 | same, but derived from the data |

Every event now resolves to a definite answer — **732 covered, 89 not covered, zero unknown**.

To regenerate it after the data changes:

```bash
python run.py publish --config config.toml --strict
```

---

## 2. Your files moved

Everything moved from the repo root into `web/`, because that's the folder GitHub Pages publishes.
Filenames are unchanged, so whatever generated the component still works.

```
web/
  index.html                            ← new: redirects to the dashboard
  Data Availability Dashboard.dc.html
  campaign-data.js
  support.js
  _ds/
  data/2024-campaign.json               ← generated, committed
  reference.html                        ← my throwaway renderer, ignore it
```

`uploads/`, `design-notes-2026-08.md` and `.thumbnail` stayed at the root — nothing references them.

---

## 3. Changes inside your code

Grouped by why. **Please review these carefully — they're in your files, not mine.**

### To load real data

- `campaign-data.js` — added `loadDataset()`. Fetches the JSON, falls back to `buildDataset()` if
  it's missing, and logs which one it used. `buildDataset()` itself is untouched.
- Added `mergeInstruments()` — the generated file names instruments; your list still supplies
  channels and short labels, because those are properties of the hardware and of the design, not of
  the availability record.
- `componentDidMount` now calls `loadDataset()` and awaits it.

### Bugs that only appeared once the data was real

- **`this.solar` crash.** Loading became async (a 500 KB fetch instead of a synchronous call), which
  widened a gap where a render could land before `solar` was assigned. `solar`/`moon` are now
  assigned before the await, and `skyBands` returns empty rather than throwing if it's called early.
  One exception in a render was taking down the whole page.
- **Coverage didn't draw at all.** The lane filter required `record.channel === channelId`. Our
  records have no channel — the season sheet logged hours per instrument, not per channel — so
  nothing matched and every lane looked like downtime. Now a record with no channel belongs to the
  instrument as a whole.
- **Overlap was always empty.** `usable()` requires `clockQuality === 'disciplined'`, and nothing in
  the record states clock discipline, so every record read `unknown` and was excluded. The
  `includeUndisciplined` prop default is now `true` in the `data-props` declaration. Chip wording
  changed from "undisciplined clocks INCLUDED" to **"unverified clocks INCLUDED"** — these clocks
  aren't known to be bad, they're unrecorded, and that's a different claim.
- **Overlap cache went stale.** The cache key didn't include the records, so an overlap computed
  before the dataset finished loading could be returned for the rest of the session. `_recKey` is
  now part of the key.
- **Zoom couldn't reach the whole season.** `setSpan` clamped the width but centred on wherever you
  were, so full zoom-out overhung one end and fell short of the other. It now snaps to the campaign
  exactly. Invisible before, because the campaign used to be a whole year.

### Pre-existing bugs

- **Ribbon drag threw** on the first mousemove — its `_drag` never carried the `el` that
  `onWindowMove` measures against. The context band always did. Fixed, plus a guard.
- **Wheel zoom fought the page.** React registers wheel listeners passively, so the
  `preventDefault()` calls in the three wheel handlers were ignored: the chart zoomed *and* the page
  scrolled, with a console warning each time. Added `bindWheel()`, which attaches a non-passive
  listener; removed the three dead `preventDefault()` calls.

### Honesty fixes

- **The SYNTHETIC banner was hardcoded.** It would have kept saying *SYNTHETIC — pending
  data/2024-campaign.json* while displaying the real record. It now reads `meta.provenance`, via new
  `provenanceTag` / `provenanceNote` bindings.
- **Empty lanes now say so.** The Overlap and Interference rails are grey whether they're empty or
  not, which is unreadable. They now state which case it is — and distinguish *"none in this
  window"* from *"none in the record at all"*, because those mean very different things.

### Cosmetic

- Lane labels are clipped to the space available (`fitText`). SVG text neither wraps nor
  ellipsises, so a long instrument name ran underneath the channel label.
- Instruments only offer the channel expander when they actually have more than one channel —
  Sphere VLF (2) and Magnetometer (3) do, the others don't. Clicking a single-channel lane now does
  nothing instead of toggling invisible state.
- Added a `<title>` and a favicon.

---

## 4. How to run it

```bash
python -m http.server 8080 --directory web
```

Then `http://localhost:8080`, and **hard-refresh** (Ctrl+Shift+R) — browser caching bit me
repeatedly during this.

Sanity checks:

- Tab title reads **Data Availability Dashboard** (proves you have the new file, not a cached one)
- Banner reads **MEASURED**, top-right reads **dataset measured-v1**
- Console has no errors and no *"using the synthetic set"* message
- **29 July, Sphere VLF** — one long run broken by damage bands: minor at 00:00, **major at 10:03**,
  minor at 12:00, 13:00 and 15:00. That's the season sheet's own data-loss column.
- **1–3 September** — a continuous 43.7-hour overlap block, the longest in the season

Expected to be empty, correctly: NimbusTrace and SuperSID lanes (neither existed in 2024), and all
interference records (we hold none).

---

## 5. Still yours — the cosmetics

There are visual issues I haven't touched. They're yours; I've only fixed things that made the page
wrong rather than ugly.

---

## 6. Your call, and it needs a design — the two-year gap

This is the one real design problem, and I'd rather you decided it than have it emerge by accident.

**The situation.** The record currently holds the **2024 season** (26 Jul – 9 Sep). Real **2026**
data starts arriving in a few weeks. Different instruments in each: NimbusTrace and SuperSID didn't
exist in 2024; the Sphere antenna, magnetometer and sky camera aren't running in 2026.

**Why it breaks the current design.** The dashboard has *one* campaign window and the ribbon never
changes scale. Load both years and that window stretches across ~two years, of which roughly 95% is
empty. The orientation frames stop orienting.

**What's already handled.** The gap between seasons falls outside every instrument's characterised
period, so it reports as **unknown** — not as downtime. That distinction is correct in the data and
you don't need to defend it in the display.

**What isn't.** How the page presents two disjoint campaigns.

My instinct is a campaign selector — treat them as two separate campaigns rather than one record
with a hole, since the instruments differ too. But it's your call and there may be better answers.

One mechanical detail whichever way you go: the dashboard fetches the literal filename
`data/2024-campaign.json`. That name needs to stop being year-specific — in `campaign-data.js`
(`DATASET_URL`) and in `config.toml` (`[campaign] path`).

---

## 7. If you change the data shape

`docs/data-format.md` is the contract. It documents both formats — the general one and the
dashboard's — and the rules the export follows, chiefly: **only fields we actually hold are
emitted**, so an absent field means *not determined* rather than a value someone invented. Three
separate bugs in this round came from a field we don't populate rendering as though it were
negative. If you need a field the record doesn't carry, tell me and I'll add it properly rather
than defaulting it.
