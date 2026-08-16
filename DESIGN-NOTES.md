# Ionosphere Data Availability Dashboard — design notes, round one

Companion to `GUI-DESIGN-BRIEF.md`. These are the notes that were on the dashboard's first screen; the dashboard itself is now the work, not the commentary.

---

## What your answers settled

| Question | Answer | Design consequence |
|---|---|---|
| Primary user (§0.1 Q1) | Both equally | Two entry points, one record set: **Timeline** for "can I use last night", **Campaign** for "what did we yield". The `entry` prop decides which one opens. |
| Scanner (§0.1 Q2) | Being written | Every block-(b) field renders as `unchecked` / `unknown` rather than assumed, and management mode is a full editing surface, not a note field. |
| Where edits go (§0.1 Q3) | localStorage + export patch | Edits are a visible local layer with a persistent banner and an explicit export. The JSON file in the repo stays the source of truth. |
| Merge policy | Scanner wins | Scanner-owned fields are locked in the editor. Dispute them instead of overwriting them. |
| Peak density | 10–50 a night | The campaign overview stays a timeline; the matrix is offered beside it, not instead of it. |
| Instruments | The five listed | Lanes collapse to instruments and expand to channels — eight channel rows at full expansion. |
| Print | Occasionally | Every encoding is fill texture, edge treatment, or glyph; colour never carries status alone. A greyscale check sits in the header. |

## Still blocking

**§9 — real data.** The file attached to the brief is an audio peak file (`.pkf`), not campaign data. Until `data/2024-campaign.json` exists, campaign-scale density in the prototype is invented, and every layout that depends on how crowded a night really is stays provisional. A lossy dump beats a curated sample; a raw CSV export of every sheet tab is enough.

**Deferred by scope.** Geometry confound checking (§7.5) needs the `site` object — the coordinates in the prototype are assumed and badged as such.

---

## Information architecture: four views, one data layer

| View | Role | What it does | Brief questions it answers |
|---|---|---|---|
| **Timeline** | Entry — analyst | Continuous zoom from 45 days to 10 seconds. Quality in the mark, overlap derived directly beneath its inputs, events as intervals. | §7 Q1–5, Q8 |
| **Campaign** | Entry — reporting | Yield totals with their filter stated, the whole run as a timeline, and the sheet's day × hour matrix beside it. | §7 Q6–7 |
| **Records** | Audit | One sortable, filterable, exportable row per record. What makes a quoted number checkable. | §7 Q7–9 |
| **Manage** | A mode, not a page | Edits happen in the inspector against the mark you are looking at. No password screen — the page is static and world-readable. | §11.8 |

**Why three read views rather than one.** The timeline is the only thing that can express a 20-minute window and a 41-hour window truthfully, but it can only show what fits on screen, so it cannot answer "how much did the campaign yield". The matrix answers that in one screen and nothing else does. The table is what makes a number citable — sortable, filterable, exportable, the property that makes CNEOS useful. They share one record set and cross-link both ways: a matrix cell jumps the timeline to that hour, a table row selects the mark.

**Manage is a mode.** The current site's worst structural problem is that you cannot see the data while editing it, so editing happens in the inspector beside the timeline, against the mark in view.

---

## The orientation solution (§11.2)

**1. Two nested frames, always on.** The campaign ribbon never changes scale: 45 days, five instrument density rows, an events sparkline, shower peaks, and your viewport drawn as a brush. Below it a context frame at 18× your current span appears once you pass roughly two days, so there is always a wider shot carrying day labels. Drag either to travel.

**2. The axis carries the date.** A day tier above the time ticks labels every UTC day in view, so the `00:00 · 12:00 · 00:00 · 12:00` failure cannot happen. A breadcrumb names the level you are at and each step is a click back out. A resolution readout states what one pixel is worth.

**3. The sky strip is a landmark.** Day, civil, nautical and astronomical twilight run under the axis at every zoom. Nights become countable, so you keep your place by shape rather than by reading labels — and the same layer stops optical coverage reading as solid green at noon.

---

## Failure states, and how to reach them

| State | Behaviour | Reach it |
|---|---|---|
| Empty window | Shows what is missing and how far the nearest record is, in either direction. | Timeline → **Gap** |
| Sparse window | One 20-minute window on 14 Aug, alone in its night: still visible at three-hour zoom, and the lane says the rest is absence, not failure. | Timeline → **20 min** |
| Undrawable records | Three records cannot be drawn — end before start, unknown instrument, unparseable timestamp. They sit in the header banner and count as excluded from every total. | Always visible in the banner |
| Disputed value | The magnitude −23 AMS report and the `End 15:25` row are flagged, not deleted — the sheet's "← Error in sheet?" became a mechanism. | Records → search `disputed`, or Timeline → 26 Jul 05:48 |

---

## Encodings

**Quality (§11.3)** — four fills for validation (`valid`, `bad-looking`, `invalid/corrupt`, `unchecked`), a bottom strip for loss severity, three edge treatments (ongoing feathers out, a derived end draws a dashed lower-bound extension rather than a hard edge, a disputed record dashes its outline), and a dotted underline for an undisciplined clock. All survive greyscale.

**Event uncertainty (§11.4)** — three treatments, the minimum that does the job:

1. **Interval bracket** with a tick at the best estimate. The interval is the primary mark; the point estimate is secondary.
2. **Minimum-width bracket** with a `±` caret when the interval is narrower than one pixel, so a sub-pixel uncertainty never renders as false precision.
3. **Night-spanning dotted line with a `?`** when `uncertaintyBasis` is `unknown` — this avoids both a silent zero and forcing an analyst to invent a number.

Launches draw the roughly ten-minute ionospherically relevant window solid and the mission timeline dotted, so a 3 h 18 min mission never reads as a 3 h 18 min confound.

**Overlap (§2)** — computed over usable coverage only (files intact, signal valid, clock disciplined), with the filter shown on screen as chips and a minimum-duration threshold. Segments contributed by a free-running clock are hatched and excluded from headline totals.

---

## Next, if this direction is right

1. The quality language deserves its own round — four fills, three edge treatments and one underline is a proposal, not a settled vocabulary.
2. Event uncertainty across three orders of magnitude is implemented but only tested against invented spreads. It needs real AMS precision data.
3. The dense 2024-08-02 05:00 hour is reachable from the toolbar; the confound-versus-meteor ambiguity treatment there wants a read before refinement.
4. Geometry: with a `site` object the page can show whether a launch plume plausibly touched the propagation path, instead of only whether it was near in time.
