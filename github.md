# github.md

repo: SEAL-Embedded/Meteor-Shower-Data-Availability
branch: main

## Last sync

date: 2026-08-16T05:05:00Z

### Updated in this project

- Read the current implementation (`Final Webpage.html`) to confirm the brief's §4 diagnoses against real code before redesigning.
- Built a replacement dashboard as a Design Component: four views (Design notes, Timeline, Campaign, Records) over one record set.
- Removed the Firebase/Firestore data layer, the CDN dependencies, and the admin password screen — data is a static JSON module; edits are a local layer with an export patch.
- Dataset stands in for `data/2024-campaign.json`, badged synthetic; the §9 cases from the brief are carried as real records.

## Screen map

| Project screen | Built from |
| --- | --- |
| Data Availability Dashboard.dc.html → Timeline | `Final Webpage.html` (zoom/pan mechanics, SVG timeline, tick computation) kept in spirit; lanes, quality encoding, orientation frames redesigned per brief §2, §4, §10 |
| Data Availability Dashboard.dc.html → Campaign | 2024 "Big Sheet" per-instrument day × hour matrix tabs (brief §3), plus yield aggregates the sheet could not compute |
| Data Availability Dashboard.dc.html → Records | `Final Webpage.html` records table + CSV/JSON export; extended per brief §8 field list |
| Data Availability Dashboard.dc.html → Manage mode | `Final Webpage.html` admin view, restructured: no password, inline with the data, localStorage + export patch |
| campaign-data.js | Brief §8 data model and §9 worked cases; no repo source (no scanner exists yet) |

## Notes

- The repo has no `data/` directory and no scanner script. `Final Webpage.html` reads from a hosted Firestore collection with a `localStorage` fallback — the redesign drops that entirely, per brief §4 and §10.
- Not synced back: nothing in this project has been written to the repo.
