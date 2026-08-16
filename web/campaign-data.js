// Dataset for the Ionosphere Data Availability dashboard.
//
// PROVENANCE, stated up front because the brief (§9) makes it a blocking issue:
// records tagged provenance:'sheet_2024' are the real cases quoted in the brief.
// Everything else is SYNTHETIC filler generated here so whole-campaign layouts
// have plausible density to be judged against. The dashboard badges this.
// data/2024-campaign.json now exists: it is generated from the lab record by
//   python -m availability publish --config config.toml
// Call loadDataset() to use it. It falls back to buildDataset() when the file is
// absent or unreadable, so the dashboard still runs standalone — and it says which
// one it used, because a synthetic set that looks real is the worse failure.

const D = (s) => Date.parse(s + (s.endsWith('Z') ? '' : 'Z'));
const H = 3600e3, M = 60e3;

export const SITE = {
  label: 'Site (ASSUMED — needs lab confirmation)',
  lat: 47.6553, lon: -122.3035, altM: 45,
  assumed: true,
  vlfPath: { from: 'NLK Jim Creek, WA (24.8 kHz)', to: 'site', note: 'path endpoints unconfirmed' },
  cameraFovDeg: 180
};

export const CAMPAIGN = { start: D('2024-07-26T00:00:00'), end: D('2024-09-09T00:00:00') };

export const DATASET_META = {
  datasetVersion: 'synthetic-v0.3',
  scanTimestamp: '2026-08-16T04:00:00Z',
  scannerVersion: 'none — no scanner exists yet (§0.1 Q2)',
  provenance: 'synthetic',
  sourceNote: 'Real cases quoted from the 2024 “Big Sheet”; all other density is invented.'
};

export const INSTRUMENTS = [
  { id: 'sphere_antenna', name: 'Sphere VLF Antenna', short: 'Sphere VLF', modality: 'radio',
    channels: [{ id: 'ch0', label: 'ch0' }, { id: 'ch1', label: 'ch1' }] },
  { id: 'magnetometer', name: 'Vectaire Magnetometer', short: 'Magnetometer', modality: 'field',
    note: 'relative field, not absolute',
    channels: [{ id: 'x', label: 'X' }, { id: 'y', label: 'Y' }, { id: 'z', label: 'Z' }] },
  { id: 'skycam', name: 'Sky Camera', short: 'Sky Camera', modality: 'optical', cadenceSec: 30,
    channels: [{ id: 'all_sky', label: 'all-sky' }] },
  { id: 'supersid', name: 'SuperSID', short: 'SuperSID', modality: 'radio',
    channels: [{ id: 'nlk', label: 'NLK 24.8 kHz' }] },
  { id: 'nimbustrace', name: 'NimbusTrace', short: 'NimbusTrace', modality: 'radio',
    channels: [{ id: 'ch0', label: 'ch0' }] }
];

export const SHOWERS = [
  { name: 'S. δ-Aquariids', peak: D('2024-07-30T12:00:00') },
  { name: 'α-Capricornids', peak: D('2024-07-30T20:00:00'), note: 'slow bright fireballs' },
  { name: 'Perseids max', peak: D('2024-08-12T14:00:00'), major: true, note: 'waxing gibbous Moon suppresses optical reporting' },
  { name: 'κ-Cygnids', peak: D('2024-08-17T12:00:00') },
  { name: 'Aurigids', peak: D('2024-09-01T00:00:00') }
];

// ── solar position (NOAA low-precision) ───────────────────────────────────────
export function solarAltitude(ms, lat = SITE.lat, lon = SITE.lon) {
  const n = ms / 86400e3 + 2440587.5 - 2451545.0;
  const rad = Math.PI / 180;
  const L = (280.460 + 0.9856474 * n) * rad;
  const g = (357.528 + 0.9856003 * n) * rad;
  const lam = L + 1.915 * rad * Math.sin(g) + 0.020 * rad * Math.sin(2 * g);
  const eps = (23.439 - 0.0000004 * n) * rad;
  const dec = Math.asin(Math.sin(eps) * Math.sin(lam));
  const ra = Math.atan2(Math.cos(eps) * Math.sin(lam), Math.cos(lam));
  let gmst = (18.697374558 + 24.06570982441908 * n) % 24;
  if (gmst < 0) gmst += 24;
  const lst = (gmst * 15 + lon) * rad;
  const ha = lst - ra;
  const phi = lat * rad;
  return Math.asin(Math.sin(phi) * Math.sin(dec) + Math.cos(phi) * Math.cos(dec) * Math.cos(ha)) / rad;
}

// Illuminated fraction only. Moon ALTITUDE is deliberately not synthesised —
// the brief forbids displaying a status not derived from a record.
export function moonIllumination(ms) {
  const synodic = 29.530588853 * 86400e3;
  const newMoon = D('2024-08-04T11:13:00');
  const phase = (((ms - newMoon) % synodic) + synodic) % synodic / synodic;
  return { fraction: (1 - Math.cos(2 * Math.PI * phase)) / 2, phase };
}

function mulberry32(a) {
  return function () {
    a |= 0; a = a + 0x6D2B79F5 | 0;
    let t = Math.imul(a ^ a >>> 15, 1 | a);
    t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t;
    return ((t ^ t >>> 14) >>> 0) / 4294967296;
  };
}

const cov = (o) => Object.assign({
  recordKind: 'coverage', id: '', instrumentId: '', channel: '', start: 0, end: 0,
  ongoing: false, validation: 'unchecked', status: 'unknown', checkMethod: 'none',
  lossSeverity: 'none', lossRecoverable: null, lossQuantity: null, lossUnit: null, lossDurationSec: null,
  endBasis: 'observed', endUncertaintySec: 0, clockQuality: 'unknown', timeScale: 'utc',
  publishState: 'draft', disputed: false, disputeNote: '', processingResultUrl: '', processingConclusion: '',
  referenceUrl: '', label: '', enteredBy: '', provenance: 'synthetic'
}, o);

const evt = (o) => Object.assign({
  recordKind: 'event', id: '', eventClass: 'meteor', eventSource: 'ams', start: 0, eventEnd: null,
  uncertaintyBasis: 'unknown', uncertaintySec: null, uncertaintyLowSec: null, uncertaintyHighSec: null,
  eventDurationSec: null, durationBinLowSec: null, durationBinHighSec: null,
  magnitudeValue: null, magnitudeBasis: null, eventLocation: '', eventVehicle: '', eventRefId: '',
  witnessCount: null, disputed: false, disputeNote: '', publishState: 'draft',
  referenceUrl: '', label: '', provenance: 'synthetic', confoundWindowSec: null
}, o);

// ── nightly dark windows, used to shape sky-cam coverage ─────────────────────
function darkWindows() {
  const out = [];
  let t = CAMPAIGN.start, prevDark = solarAltitude(t) < -6, open = prevDark ? t : null;
  for (t += 5 * M; t <= CAMPAIGN.end; t += 5 * M) {
    const dark = solarAltitude(t) < -6;
    if (dark && !prevDark) open = t;
    if (!dark && prevDark && open != null) { out.push([open, t]); open = null; }
    prevDark = dark;
  }
  if (open != null) out.push([open, CAMPAIGN.end]);
  return out;
}

export function buildDataset() {
  const coverage = [], events = [], configs = [], interference = [], broken = [];
  const rnd = mulberry32(20240726);
  let seq = 0;
  const nid = (p) => `${p}_${(++seq).toString(36)}`;

  // ── (1) real cases from the 2024 sheet ────────────────────────────────────
  coverage.push(cov({ id: 'cov_sheet_long', instrumentId: 'sphere_antenna', channel: 'ch0',
    start: D('2024-08-01T00:20:00'), end: D('2024-08-02T17:10:00'),
    validation: 'valid', status: 'ok', checkMethod: 'size_only', clockQuality: 'disciplined',
    publishState: 'publishable', provenance: 'sheet_2024', label: 'Long clean window (≈41 h)',
    referenceUrl: '#archive/sphere/2024-08-01', enteredBy: 'sheet import' }));
  coverage.push(cov({ id: 'cov_sheet_long_ch1', instrumentId: 'sphere_antenna', channel: 'ch1',
    start: D('2024-08-01T00:20:00'), end: D('2024-08-02T17:10:00'),
    validation: 'bad_looking', status: 'ok', checkMethod: 'size_only', clockQuality: 'disciplined',
    publishState: 'draft', provenance: 'sheet_2024',
    label: 'ch1 valid files, signal visibly unusable', enteredBy: 'sheet import' }));

  [[null, '2024-08-06T10:58:00'], ['2024-08-06T12:23:00', '2024-08-06T14:23:00'],
   ['2024-08-06T16:23:00', '2024-08-06T17:23:00']].forEach(([s, e], i) => {
    ['ch0', 'ch1'].forEach((ch) => coverage.push(cov({
      id: nid('cov_frag'), instrumentId: 'sphere_antenna', channel: ch,
      start: s ? D(s) : D('2024-08-06T09:12:00'), end: D(e),
      endBasis: i === 0 ? 'derived_from_filesize' : 'observed',
      endUncertaintySec: i === 0 ? 900 : 0,
      validation: 'valid', status: i === 0 ? 'partial' : 'ok', checkMethod: 'size_only',
      clockQuality: 'disciplined', publishState: 'draft', provenance: 'sheet_2024',
      label: i === 0 ? 'Fragmented day — start not recorded in sheet' : 'Fragmented day' })));
  });

  coverage.push(cov({ id: 'cov_short_20m', instrumentId: 'sphere_antenna', channel: 'ch0',
    start: D('2024-08-14T01:00:00'), end: D('2024-08-14T01:20:00'),
    validation: 'valid', status: 'ok', checkMethod: 'size_only', clockQuality: 'disciplined',
    publishState: 'publishable', provenance: 'sheet_2024', label: '20-minute window' }));

  coverage.push(cov({ id: 'cov_skycam_partial', instrumentId: 'skycam', channel: 'all_sky',
    start: D('2024-07-27T05:00:00'), end: D('2024-07-27T06:13:00'),
    validation: 'valid', status: 'partial', checkMethod: 'parse_only',
    lossSeverity: 'minor', lossRecoverable: false, lossQuantity: 60, lossUnit: 'snaps', lossDurationSec: 1800,
    clockQuality: 'disciplined', publishState: 'publishable', provenance: 'sheet_2024',
    label: 'Last half hour data lost', processingConclusion: 'Usable up to 05:30 only.' }));

  coverage.push(cov({ id: 'cov_major_loss', instrumentId: 'magnetometer', channel: 'y',
    start: D('2024-07-29T08:00:00'), end: D('2024-07-29T12:40:00'),
    validation: 'invalid', status: 'corrupt', checkMethod: 'sha256',
    lossSeverity: 'major', lossRecoverable: false, lossDurationSec: 4200,
    clockQuality: 'disciplined', publishState: 'draft', provenance: 'sheet_2024',
    label: 'Major Data Lost (from 10:03)' }));

  coverage.push(cov({ id: 'cov_minor_73', instrumentId: 'skycam', channel: 'all_sky',
    start: D('2024-08-09T04:00:00'), end: D('2024-08-09T11:30:00'),
    validation: 'valid', status: 'partial', checkMethod: 'parse_only',
    lossSeverity: 'minor', lossRecoverable: true, lossQuantity: 73, lossUnit: 'snaps', lossDurationSec: 2190,
    clockQuality: 'disciplined', publishState: 'published', provenance: 'sheet_2024',
    label: 'Minor data loss (73 snaps) / Can be Recovered',
    processingResultUrl: '#archive/results/2024-08-09-skycam' }));

  coverage.push(cov({ id: 'cov_disputed_end', instrumentId: 'sphere_antenna', channel: 'ch0',
    start: D('2024-08-21T03:00:00'), end: D('2024-08-21T15:25:00'),
    validation: 'unchecked', status: 'suspect', checkMethod: 'none', clockQuality: 'unknown',
    publishState: 'draft', disputed: true,
    disputeNote: 'End 15:25 recorded in the 03:00 row — “← Error in sheet?”. Needs review before this window counts toward any total.',
    provenance: 'sheet_2024', label: 'End 15:25 in the 03:00 row' }));

  // ── (2) real events ───────────────────────────────────────────────────────
  const ams = [
    ['3647f', '2024-07-26T05:48:00', 'MA', 3.5, -23, 180, 41,
     'Magnitude −23 is ~13,000× the full Moon — superbolide class. Almost certainly witness over-estimation.'],
    ['3646b', '2024-07-27T00:50:00', 'MA', 7.5, -11, 180, 12, ''],
    ['3901d', '2024-08-02T05:45:00', 'SC', 7.5, -13, 90, 27, ''],
    ['3908a', '2024-08-02T09:04:00', 'MO', 3.5, -16, 180, 9, ''],
    ['4676j', '2024-08-27T05:20:00', 'WI', 3.5, -11, 300, 5, '']
  ];
  ams.forEach(([id, t, loc, dur, mag, unc, wit, dispute]) => events.push(evt({
    id: 'evt_ams_' + id, eventClass: dur >= 4 || mag <= -4 ? 'fireball' : 'meteor', eventSource: 'ams',
    start: D(t), uncertaintyBasis: 'reported_precision', uncertaintySec: unc,
    eventDurationSec: dur, durationBinLowSec: Math.floor(dur), durationBinHighSec: Math.ceil(dur),
    magnitudeValue: mag, magnitudeBasis: 'witness_estimate', eventLocation: loc, eventRefId: 'AMS ' + id,
    witnessCount: wit, disputed: !!dispute, disputeNote: dispute, publishState: 'publishable',
    provenance: 'sheet_2024', referenceUrl: '#ams/' + id,
    label: 'AMS ' + id + ' · ' + loc + ' · mag ' + mag })));

  events.push(evt({ id: 'evt_launch_f9', eventClass: 'launch', eventSource: 'launch',
    start: D('2024-08-02T05:01:00'), eventEnd: D('2024-08-02T08:19:00'),
    uncertaintyBasis: 'reported_precision', uncertaintySec: 5,
    eventVehicle: 'Falcon 9 Block 5', eventLocation: 'Kennedy Space Center, FL',
    confoundWindowSec: 600, publishState: 'publishable', provenance: 'sheet_2024',
    label: 'Falcon 9 Block 5 — KSC',
    disputeNote: 'Mission End 08:19 is the mission timeline. Ionospherically relevant window ≈ first 10 min (ascent plume, acoustic-gravity waves) plus any deorbit burn.' }));

  ['2024-07-29T03:45:12', '2024-07-29T03:49:38', '2024-07-29T03:53:04'].forEach((t, i) => events.push(evt({
    id: 'evt_spot_' + i, eventClass: 'meteor', eventSource: 'skycam_spot', start: D(t),
    uncertaintyBasis: 'instrumental_fit', uncertaintySec: 0.5, eventDurationSec: 0.9,
    publishState: 'published', provenance: 'sheet_2024', label: 'Sky-cam spot detection' })));

  events.push(evt({ id: 'evt_unknown_timing', eventClass: 'meteor', eventSource: 'ams',
    start: D('2024-08-13T07:00:00'), uncertaintyBasis: 'unknown', uncertaintySec: null,
    eventLocation: 'OR', magnitudeValue: -6, magnitudeBasis: 'witness_estimate',
    eventRefId: 'AMS 4102z', publishState: 'draft', provenance: 'sheet_2024',
    label: 'AMS 4102z — reporter gave no time of night' }));

  events.push(evt({ id: 'evt_nasa_1', eventClass: 'fireball', eventSource: 'nasa_allsky',
    start: D('2024-08-12T09:31:22'), uncertaintyBasis: 'instrumental_fit', uncertaintySec: 1,
    eventDurationSec: 2.4, magnitudeValue: -5.1, magnitudeBasis: 'instrumental_photometry',
    publishState: 'published', provenance: 'sheet_2024',
    label: 'NASA All Sky — triangulated', referenceUrl: '#nasa/allsky' }));
  events.push(evt({ id: 'evt_flare', eventClass: 'solar_flare', eventSource: 'nasa_allsky',
    start: D('2024-08-05T18:12:00'), eventEnd: D('2024-08-05T18:41:00'),
    uncertaintyBasis: 'instrumental_fit', uncertaintySec: 60, publishState: 'publishable',
    provenance: 'synthetic', label: 'M-class flare — classic SID confound' }));

  // ── (3) synthetic coverage density ────────────────────────────────────────
  const dark = darkWindows();
  dark.forEach(([s, e], i) => {
    if (rnd() < 0.12) return; // clouded / down nights
    const st = s + rnd() * 25 * M, en = e - rnd() * 25 * M;
    if (en - st < 20 * M) return;
    const overlapsReal = st < D('2024-07-27T06:13:00') && en > D('2024-07-27T05:00:00');
    const overlapsReal2 = st < D('2024-08-09T11:30:00') && en > D('2024-08-09T04:00:00');
    if (overlapsReal || overlapsReal2) return;
    const bad = rnd() < 0.15;
    coverage.push(cov({ id: nid('cov_sky'), instrumentId: 'skycam', channel: 'all_sky',
      start: st, end: en, validation: bad ? 'bad_looking' : 'valid',
      status: rnd() < 0.12 ? 'partial' : 'ok', checkMethod: 'parse_only',
      lossSeverity: rnd() < 0.14 ? 'minor' : 'none',
      lossQuantity: null, lossUnit: 'snaps',
      lossDurationSec: null, clockQuality: 'disciplined',
      publishState: i < 18 ? (rnd() < 0.5 ? 'published' : 'publishable') : 'draft',
      label: bad ? 'Overcast — stars not resolved' : 'Nightly all-sky run' }));
  });
  coverage.forEach((c) => {
    if (c.instrumentId === 'skycam' && c.lossSeverity === 'minor' && c.lossQuantity == null) {
      c.lossQuantity = 10 + Math.floor(rnd() * 90);
      c.lossDurationSec = c.lossQuantity * 30;
      c.lossRecoverable = rnd() < 0.5;
      c.status = 'partial';
    }
  });

  const spans = (laneStart, laneEnd, meanH, gapMeanH) => {
    const out = []; let t = laneStart;
    while (t < laneEnd) {
      const dur = (0.35 + rnd() * 1.7) * meanH * H;
      const end = Math.min(t + dur, laneEnd);
      out.push([t, end]);
      t = end + (0.2 + rnd() * 1.9) * gapMeanH * H;
    }
    return out;
  };

  const occupied = (id, ch, s, e) => coverage.some((c) =>
    c.instrumentId === id && c.channel === ch && c.start < e && c.end > s);

  spans(CAMPAIGN.start, CAMPAIGN.end, 16, 3).forEach(([s, e]) => {
    ['ch0', 'ch1'].forEach((ch) => {
      if (occupied('sphere_antenna', ch, s, e)) return;
      const r = rnd();
      coverage.push(cov({ id: nid('cov_sph'), instrumentId: 'sphere_antenna', channel: ch,
        start: s, end: e,
        validation: ch === 'ch1' && r < 0.22 ? (r < 0.09 ? 'invalid' : 'bad_looking') : (r < 0.06 ? 'unchecked' : 'valid'),
        status: r < 0.08 ? 'partial' : 'ok', checkMethod: r < 0.3 ? 'sha256' : 'size_only',
        lossSeverity: r < 0.1 ? 'minor' : 'none', lossRecoverable: r < 0.1 ? r < 0.05 : null,
        lossDurationSec: r < 0.1 ? Math.floor(r * 3000) : null,
        clockQuality: 'disciplined', endBasis: 'observed',
        publishState: s < D('2024-08-16T00:00:00') ? (r < 0.4 ? 'published' : 'publishable') : 'draft',
        label: 'Sphere capture' }));
    });
  });

  spans(CAMPAIGN.start, CAMPAIGN.end, 40, 1.5).forEach(([s, e]) => {
    ['x', 'y', 'z'].forEach((ch) => {
      if (occupied('magnetometer', ch, s, e)) return;
      const r = rnd();
      coverage.push(cov({ id: nid('cov_mag'), instrumentId: 'magnetometer', channel: ch,
        start: s, end: e,
        validation: ch === 'z' && r < 0.3 ? 'invalid' : (r < 0.08 ? 'bad_looking' : 'valid'),
        status: 'ok', checkMethod: 'crc32', clockQuality: 'disciplined',
        publishState: r < 0.35 ? 'published' : 'publishable',
        label: ch === 'z' && r < 0.3 ? 'Z component railed' : 'Magnetometer run' }));
    });
  });

  // SuperSID: derived ends and a long free-running stretch — the overlap trap.
  spans(CAMPAIGN.start, CAMPAIGN.end, 22, 2).forEach(([s, e]) => {
    if (occupied('supersid', 'nlk', s, e)) return;
    const free = s > D('2024-08-10T00:00:00') && s < D('2024-08-24T00:00:00');
    coverage.push(cov({ id: nid('cov_sid'), instrumentId: 'supersid', channel: 'nlk',
      start: s, end: e, endBasis: 'derived_from_samples',
      endUncertaintySec: Math.round((e - s) / 1000 * 0.002),
      sampleRateHz: 96000, validation: 'unchecked', status: 'unknown', checkMethod: 'none',
      clockQuality: free ? 'free_running' : 'disciplined', timeScale: 'unknown',
      publishState: 'draft',
      label: free ? 'NLK 24.8 kHz — clock free-running' : 'NLK 24.8 kHz' }));
  });

  spans(D('2024-08-18T00:00:00'), CAMPAIGN.end, 9, 14).forEach(([s, e]) => {
    coverage.push(cov({ id: nid('cov_nim'), instrumentId: 'nimbustrace', channel: 'ch0',
      start: s, end: e, validation: 'unchecked', status: 'unknown', checkMethod: 'none',
      clockQuality: 'unknown', timeScale: 'unknown', provenance: 'manual', publishState: 'draft',
      label: 'NimbusTrace capture — hand-entered, unverified' }));
  });

  const last = coverage.filter((c) => c.instrumentId === 'sphere_antenna').sort((a, b) => b.end - a.end)[0];
  if (last) { last.ongoing = true; last.end = CAMPAIGN.end; last.endBasis = 'assumed_ongoing'; last.label = 'Still recording at scan time'; }

  // ── (4) synthetic events at realistic density (10–50 on a busy night) ─────
  dark.forEach(([s, e]) => {
    const moon = moonIllumination((s + e) / 2).fraction;
    const perseid = Math.exp(-Math.pow((s - D('2024-08-12T14:00:00')) / (3.5 * 86400e3), 2));
    const base = 3 + 34 * perseid;
    const n = Math.round(base * (1 - 0.55 * moon) * (0.5 + rnd()));
    for (let i = 0; i < n; i++) {
      const u = Math.pow(rnd(), 0.55); // density rises toward dawn
      const t = s + u * (e - s);
      const source = rnd() < 0.72 ? 'skycam_spot' : (rnd() < 0.6 ? 'ams' : 'nasa_allsky');
      const isAms = source === 'ams';
      const dur = isAms ? [1.5, 3.5, 7.5][Math.floor(rnd() * 3)] : 0.4 + rnd() * 2;
      events.push(evt({ id: nid('evt'), eventClass: isAms && rnd() < 0.4 ? 'fireball' : 'meteor',
        eventSource: source, start: t,
        uncertaintyBasis: isAms ? 'reported_precision' : 'instrumental_fit',
        uncertaintySec: isAms ? [60, 90, 180, 300][Math.floor(rnd() * 4)] : (source === 'nasa_allsky' ? 1 : 0.5),
        eventDurationSec: dur,
        durationBinLowSec: isAms ? Math.floor(dur) : null, durationBinHighSec: isAms ? Math.ceil(dur) : null,
        magnitudeValue: isAms ? -(2 + Math.floor(rnd() * 12)) : (source === 'nasa_allsky' ? -(2 + rnd() * 4) : null),
        magnitudeBasis: isAms ? 'witness_estimate' : (source === 'nasa_allsky' ? 'instrumental_photometry' : null),
        witnessCount: isAms ? 1 + Math.floor(rnd() * 20) : null,
        publishState: t < D('2024-08-16T00:00:00') ? 'publishable' : 'draft',
        label: source === 'skycam_spot' ? 'Sky-cam spot detection' : (isAms ? 'AMS report' : 'NASA All Sky') }));
    }
  });

  [['2024-07-31T22:40:00', 4 * H, 'launch', 'Falcon 9', 'Vandenberg SFB, CA', 600],
   ['2024-08-20T13:05:00', 3 * H, 'launch', 'Electron', 'Māhia, NZ', 600],
   ['2024-09-03T01:30:00', 5 * H, 'launch', 'Falcon 9', 'Cape Canaveral SFS, FL', 600]
  ].forEach(([t, mission, cls, veh, loc, confound]) => events.push(evt({
    id: nid('evt_launch'), eventClass: cls, eventSource: 'launch', start: D(t),
    eventEnd: D(t) + mission, uncertaintyBasis: 'reported_precision', uncertaintySec: 5,
    eventVehicle: veh, eventLocation: loc, confoundWindowSec: confound,
    publishState: 'publishable', label: veh + ' — ' + loc })));

  // ── (5) configuration + interference ─────────────────────────────────────
  configs.push({ recordKind: 'config', id: 'cfg_1', instrumentId: 'sphere_antenna', channel: 'ch0',
    start: D('2024-08-08T14:00:00'), end: CAMPAIGN.end, gainDb: 26, previousGainDb: 20,
    label: 'Gain 20 → 26 dB', note: 'Amplitude step at this boundary is an artefact, not a discovery.',
    provenance: 'manual' });
  configs.push({ recordKind: 'config', id: 'cfg_2', instrumentId: 'skycam', channel: 'all_sky',
    start: D('2024-08-19T02:00:00'), end: CAMPAIGN.end, cadenceSec: 15, previousCadenceSec: 30,
    label: 'Cadence 30 s → 15 s', note: 'Snap counts before and after are not comparable.',
    provenance: 'manual' });
  configs.push({ recordKind: 'config', id: 'cfg_3', instrumentId: 'supersid', channel: 'nlk',
    start: CAMPAIGN.start, end: CAMPAIGN.end, transmitter: 'NLK', frequencyKHz: 24.8,
    label: 'Monitoring NLK 24.8 kHz', provenance: 'manual' });

  interference.push({ recordKind: 'interference', id: 'int_1', kind: 'transmitter_maintenance',
    start: D('2024-08-15T08:00:00'), end: D('2024-08-15T16:00:00'), instrumentIds: ['supersid', 'sphere_antenna'],
    label: 'NLK scheduled outage — looks like a catastrophic event', provenance: 'manual' });
  interference.push({ recordKind: 'interference', id: 'int_2', kind: 'lightning_sferics',
    start: D('2024-08-03T02:00:00'), end: D('2024-08-03T09:30:00'), instrumentIds: ['sphere_antenna', 'supersid'],
    label: 'Regional storm complex — heavy sferics', provenance: 'manual' });
  interference.push({ recordKind: 'interference', id: 'int_3', kind: 'rfi_local',
    start: D('2024-08-22T17:00:00'), end: D('2024-08-22T19:15:00'), instrumentIds: ['sphere_antenna'],
    label: 'Local RFI — site generator', provenance: 'manual' });

  // ── (6) deliberately broken records, so the error state is real ──────────
  broken.push({ recordKind: 'coverage', id: 'bad_1', instrumentId: 'sphere_antenna', channel: 'ch0',
    start: D('2024-08-04T22:00:00'), end: D('2024-08-04T21:00:00'), provenance: 'sheet_2024',
    label: 'End precedes start', reason: 'end < start — interval cannot be drawn' });
  broken.push({ recordKind: 'coverage', id: 'bad_2', instrumentId: 'vlf_loop_b', channel: 'ch0',
    start: D('2024-08-11T04:00:00'), end: D('2024-08-11T07:00:00'), provenance: 'manual',
    label: 'Unknown instrument id “vlf_loop_b”', reason: 'instrumentId not in the instrument list — surfaced, not dropped' });
  broken.push({ recordKind: 'event', id: 'bad_3', eventClass: 'meteor', eventSource: 'ams',
    start: NaN, rawStart: '2024-08-2705:20', provenance: 'sheet_2024',
    label: 'Unparseable timestamp “2024-08-2705:20”', reason: 'timestamp did not parse' });

  coverage.sort((a, b) => a.start - b.start);
  events.sort((a, b) => a.start - b.start);

  return { meta: DATASET_META, site: SITE, campaign: CAMPAIGN, instruments: INSTRUMENTS,
    showers: SHOWERS, coverage, events, configs, interference, broken };
}

// ── real data ────────────────────────────────────────────────────────────────
// The generated file carries only the fields the lab actually holds. Everything it
// omits keeps the defaults in cov()/evt() above, so an absent field reads as
// "not determined" rather than as an invented value.

export const DATASET_URL = 'data/2024-campaign.json';

export async function loadDataset(url = DATASET_URL) {
  let raw;
  try {
    const response = await fetch(url, { cache: 'no-cache' });
    if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
    raw = await response.json();
  } catch (error) {
    console.warn(`[campaign-data] ${url} unavailable (${error.message}); using the synthetic set.`);
    return buildDataset();
  }

  const coverage = (raw.coverage ?? []).map((r) => cov(r)).sort((a, b) => a.start - b.start);
  const events = (raw.events ?? []).map((r) => evt(r)).sort((a, b) => a.start - b.start);

  // Showers are astronomy, not lab record, so they stay with this module. Configs,
  // interference and broken records have no source in the record yet: empty, not invented.
  return {
    meta: { ...DATASET_META, ...(raw.meta ?? {}) },
    site: raw.site ?? SITE,
    campaign: raw.campaign ?? CAMPAIGN,
    instruments: mergeInstruments(raw.instruments),
    showers: SHOWERS,
    coverage,
    events,
    configs: [],
    interference: [],
    broken: raw.broken ?? []
  };
}

// Channel lists are a property of the hardware, not of the availability record, so the
// generated file names instruments and this module keeps their channels.
function mergeInstruments(generated) {
  if (!generated?.length) return INSTRUMENTS;
  return generated.map((instrument) => {
    const known = INSTRUMENTS.find((i) => i.id === instrument.id);
    return {
      ...known,
      ...instrument,
      // Channels are a property of the hardware, so they come from this list rather than from
      // the records: the sphere antenna has two whether or not a given season logged them
      // separately. Instruments with one channel have nothing to expand into, and the dashboard
      // only offers the control where there is more than one.
      channels: known?.channels ?? [],
      // Short labels are a presentation decision made here. The generated file derives one from
      // the full name, which is long enough to collide with the channel label beside it.
      short: known?.short ?? instrument.short ?? instrument.name
    };
  });
}

