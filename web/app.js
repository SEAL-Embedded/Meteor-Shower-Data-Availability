/*
 * Reference renderer for the availability record.
 *
 * This is deliberately small. It exists to prove that docs/data-format.md is sufficient to build a
 * front end against, and to be replaced. It reads the live API when one is configured and reachable,
 * and the published snapshot otherwise -- so the page keeps working when the data machine does not.
 */

(function () {
  "use strict";

  const SVG_NS = "http://www.w3.org/2000/svg";
  const ROW_HEIGHT = 26;
  const ROW_GAP = 6;
  const MARGIN = { top: 22, right: 16, bottom: 28, left: 170 };

  const state = { feed: null, index: null, year: null };

  const el = {
    status: document.getElementById("status"),
    main: document.getElementById("main"),
    year: document.getElementById("year"),
    feed: document.getElementById("feed"),
    timeline: document.getElementById("timeline"),
    events: document.querySelector("#events tbody"),
    sources: document.getElementById("sources"),
    generated: document.getElementById("generated"),
  };

  // -- data access ---------------------------------------------------------------------------

  async function fetchJson(url, timeoutMs) {
    const controller = new AbortController();
    const timer = timeoutMs ? setTimeout(() => controller.abort(), timeoutMs) : null;
    try {
      const response = await fetch(url, { signal: controller.signal, cache: "no-cache" });
      if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
      return await response.json();
    } finally {
      if (timer) clearTimeout(timer);
    }
  }

  async function resolveFeed() {
    const base = (window.AVAILABILITY_API_BASE || "").replace(/\/+$/, "");
    if (base) {
      try {
        const index = await fetchJson(
          `${base}/api/v1/index`,
          window.AVAILABILITY_API_TIMEOUT_MS || 2500
        );
        return { kind: "live", index, year: (y) => fetchJson(`${base}/api/v1/years/${y}`) };
      } catch (error) {
        console.warn("live API unavailable, falling back to the published snapshot:", error);
      }
    }
    const index = await fetchJson("data/index.json");
    return { kind: "snapshot", index, year: (y) => fetchJson(`data/${y}.json`) };
  }

  // -- formatting ----------------------------------------------------------------------------

  const parse = (text) => new Date(text);

  function formatInstant(text) {
    const date = parse(text);
    return `${date.toISOString().slice(0, 10)} ${date.toISOString().slice(11, 16)}`;
  }

  function formatDay(date) {
    return date.toLocaleDateString("en-GB", { day: "numeric", month: "short", timeZone: "UTC" });
  }

  const VERDICT_TEXT = {
    covered: "Covered",
    partial: "Partial",
    not_covered: "Not covered",
    unknown: "Unknown",
  };

  // -- rendering: timeline -------------------------------------------------------------------

  function node(name, attrs, text) {
    const element = document.createElementNS(SVG_NS, name);
    for (const [key, value] of Object.entries(attrs || {})) {
      element.setAttribute(key, value);
    }
    if (text !== undefined) element.textContent = text;
    return element;
  }

  function drawTimeline(payload, instruments) {
    const svg = el.timeline;
    svg.replaceChildren();

    const rows = instruments.map((instrument) => ({
      id: instrument.id,
      label: instrument.name,
      bars: payload.coverage
        .filter((record) => record.instrument_id === instrument.id)
        .map((record) => ({
          start: parse(record.start),
          end: parse(record.end),
          quality: record.quality,
          title: `${instrument.name}\n${formatInstant(record.start)} – ${formatInstant(record.end)} UTC\n${record.quality}${record.note ? `\n${record.note}` : ""}`,
        })),
    }));

    rows.push({
      id: "__overlap__",
      label: "Two or more instruments",
      overlap: true,
      bars: payload.segments
        .filter((segment) => segment.degree >= 2)
        .map((segment) => ({
          start: parse(segment.start),
          end: parse(segment.end),
          quality: "overlap",
          title: `${segment.degree} instruments\n${formatInstant(segment.start)} – ${formatInstant(segment.end)} UTC\n${segment.instrument_ids.join(", ")}`,
        })),
    });

    const bounds = timeBounds(payload, rows);
    if (!bounds) {
      svg.appendChild(
        node("text", { x: 8, y: 24, class: "axis-label" }, "No coverage recorded for this year.")
      );
      svg.setAttribute("viewBox", "0 0 600 40");
      svg.setAttribute("height", 40);
      return;
    }

    const width = Math.max(svg.clientWidth || 900, 640);
    const plotWidth = width - MARGIN.left - MARGIN.right;
    const height = MARGIN.top + rows.length * (ROW_HEIGHT + ROW_GAP) + MARGIN.bottom;
    svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
    svg.setAttribute("height", height);

    const span = bounds.end - bounds.start;
    const x = (date) => MARGIN.left + ((date - bounds.start) / span) * plotWidth;

    for (const tick of ticks(bounds.start, bounds.end)) {
      const position = x(tick);
      svg.appendChild(
        node("line", {
          x1: position, x2: position,
          y1: MARGIN.top - 6, y2: height - MARGIN.bottom + 4,
          class: "grid-line",
        })
      );
      svg.appendChild(
        node(
          "text",
          { x: position, y: height - MARGIN.bottom + 18, class: "axis-label", "text-anchor": "middle" },
          formatDay(tick)
        )
      );
    }

    rows.forEach((row, index) => {
      const y = MARGIN.top + index * (ROW_HEIGHT + ROW_GAP);
      svg.appendChild(
        node("rect", {
          x: MARGIN.left, y, width: plotWidth, height: ROW_HEIGHT, rx: 3, class: "row-base",
        })
      );
      svg.appendChild(
        node(
          "text",
          { x: MARGIN.left - 10, y: y + ROW_HEIGHT / 2 + 4, class: "row-label", "text-anchor": "end" },
          row.label
        )
      );
      for (const bar of row.bars) {
        const left = x(bar.start);
        const right = x(bar.end);
        const rect = node("rect", {
          x: left,
          y: y + 3,
          width: Math.max(right - left, 1.5),
          height: ROW_HEIGHT - 6,
          rx: 2,
          class: `bar-${bar.quality}`,
        });
        rect.appendChild(node("title", {}, bar.title));
        svg.appendChild(rect);
      }
    });

    const verdicts = new Map(payload.event_coverage.map((record) => [record.event_id, record]));
    for (const event of payload.events) {
      const at = parse(event.time);
      if (at < bounds.start || at > bounds.end) continue;
      const position = x(at);
      const mark = node("line", {
        x1: position, x2: position,
        y1: MARGIN.top - 8, y2: height - MARGIN.bottom,
        class: "event-mark",
      });
      const verdict = verdicts.get(event.id);
      mark.appendChild(
        node(
          "title",
          {},
          `${event.label || event.kind}\n${formatInstant(event.time)} UTC\n${VERDICT_TEXT[verdict ? verdict.verdict : "unknown"]}`
        )
      );
      svg.appendChild(mark);
    }
  }

  function timeBounds(payload, rows) {
    const moments = [];
    for (const row of rows) {
      for (const bar of row.bars) moments.push(bar.start, bar.end);
    }
    for (const event of payload.events) moments.push(parse(event.time));
    if (!moments.length) return null;
    const start = new Date(Math.min(...moments));
    const end = new Date(Math.max(...moments));
    if (start.getTime() === end.getTime()) return null;
    return { start, end };
  }

  function ticks(start, end) {
    const days = (end - start) / 86400000;
    const step = days > 240 ? 30 : days > 60 ? 7 : days > 14 ? 2 : 1;
    const marks = [];
    const cursor = new Date(
      Date.UTC(start.getUTCFullYear(), start.getUTCMonth(), start.getUTCDate())
    );
    while (cursor <= end) {
      if (cursor >= start) marks.push(new Date(cursor));
      cursor.setUTCDate(cursor.getUTCDate() + step);
    }
    return marks;
  }

  // -- rendering: tables ---------------------------------------------------------------------

  function drawEvents(payload, instruments) {
    const names = new Map(instruments.map((instrument) => [instrument.id, instrument.name]));
    const verdicts = new Map(payload.event_coverage.map((record) => [record.event_id, record]));
    el.events.replaceChildren();

    if (!payload.events.length) {
      const row = document.createElement("tr");
      const cell = document.createElement("td");
      cell.colSpan = 4;
      cell.textContent = "No events recorded for this year.";
      row.appendChild(cell);
      el.events.appendChild(row);
      return;
    }

    for (const event of payload.events) {
      const verdict = verdicts.get(event.id) || { verdict: "unknown", covering: [] };
      const row = document.createElement("tr");

      const when = document.createElement("td");
      const stamp = document.createElement("time");
      stamp.dateTime = event.time;
      stamp.textContent = formatInstant(event.time);
      when.appendChild(stamp);

      const what = document.createElement("td");
      const description = event.label || event.kind.replace(/_/g, " ");
      if (event.url) {
        const link = document.createElement("a");
        link.href = event.url;
        link.textContent = description;
        link.rel = "noopener noreferrer";
        link.target = "_blank";
        what.appendChild(link);
      } else {
        what.textContent = description;
      }

      const where = document.createElement("td");
      where.textContent = (event.location && event.location.label) || "—";

      const outcome = document.createElement("td");
      const label = document.createElement("span");
      label.className = `verdict verdict-${verdict.verdict}`;
      label.textContent = VERDICT_TEXT[verdict.verdict] || verdict.verdict;
      outcome.appendChild(label);
      if (verdict.covering && verdict.covering.length) {
        const detail = document.createElement("span");
        detail.className = "verdict-detail";
        detail.textContent = verdict.covering
          .map((entry) => `${names.get(entry.instrument_id) || entry.instrument_id} (${entry.quality})`)
          .join(", ");
        outcome.appendChild(detail);
      }

      row.append(when, what, where, outcome);
      el.events.appendChild(row);
    }
  }

  function drawSources(index) {
    el.sources.replaceChildren();
    for (const source of index.sources) {
      const item = document.createElement("li");
      const name = document.createElement("strong");
      name.textContent = source.name;
      item.appendChild(name);

      if (source.attribution) {
        item.append(` — ${source.attribution}`);
      }
      if (source.url) {
        item.append(" ");
        const link = document.createElement("a");
        link.href = source.url;
        link.textContent = source.url;
        link.rel = "noopener noreferrer";
        link.target = "_blank";
        item.appendChild(link);
      }

      const status = document.createElement("span");
      status.className = `source-status ${source.status}`;
      status.textContent = source.detail
        ? ` ${source.status}: ${source.detail}`
        : ` ${source.status}`;
      item.appendChild(document.createElement("br"));
      item.appendChild(status);
      el.sources.appendChild(item);
    }
  }

  // -- orchestration -------------------------------------------------------------------------

  async function showYear(year) {
    const payload = await state.feed.year(year);
    state.year = payload;
    drawTimeline(payload, state.feed.index.instruments);
    drawEvents(payload, state.feed.index.instruments);
  }

  function setStatus(message, isError) {
    el.status.textContent = message;
    el.status.hidden = !message;
    el.status.classList.toggle("error", Boolean(isError));
  }

  async function start() {
    try {
      state.feed = await resolveFeed();
    } catch (error) {
      setStatus(
        "The availability record could not be loaded. No snapshot has been published yet, or it is unreachable.",
        true
      );
      return;
    }

    const index = state.feed.index;
    drawSources(index);
    el.generated.textContent = `Record generated ${formatInstant(index.generated_at)} UTC.`;
    el.feed.textContent =
      state.feed.kind === "live" ? "live from the instrument record" : "published snapshot";

    if (!index.years.length) {
      setStatus("No coverage has been ingested yet.", false);
      el.main.hidden = false;
      return;
    }

    el.year.replaceChildren();
    for (const year of [...index.years].reverse()) {
      const option = document.createElement("option");
      option.value = year;
      option.textContent = year;
      el.year.appendChild(option);
    }
    el.year.addEventListener("change", () => showYear(Number(el.year.value)));

    await showYear(Number(el.year.value));
    setStatus("", false);
    el.main.hidden = false;

    let resizeTimer = null;
    window.addEventListener("resize", () => {
      clearTimeout(resizeTimer);
      resizeTimer = setTimeout(() => {
        if (state.year) drawTimeline(state.year, index.instruments);
      }, 150);
    });
  }

  start();
})();
