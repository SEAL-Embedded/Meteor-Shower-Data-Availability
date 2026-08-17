"""Human corrections, merged over the generated record at publish time.

The dashboard's manage mode lets someone mark a record disputed, write a processing conclusion, or
move it to ``published``. Those edits lived in one browser's local storage and could be exported as
a patch, and nothing read the patch -- so the next ``publish`` regenerated the record without them.
This is the reader that closes that loop.

**The merge policy is the lab's, recorded in docs/design-notes-2026-08.md: the scanner wins.** An
adapter's output is what a source said, and a correction must not quietly rewrite it. So only
fields no adapter produces are applied; a correction naming anything else is refused and reported
rather than merged. Disagreeing with a scanned value is what ``disputed`` is for -- the record keeps
what the source said and carries the objection alongside it, which is the same distinction the rest
of this project rests on.

**Refusals are loud.** An unknown record id, or a correction to a source-owned field, becomes a
warning on the store. ``publish --strict`` already refuses to write when warnings are present, so a
patch that does not apply cleanly stops the publish instead of silently doing half of itself.

The file read here is exactly what the dashboard's *Export patch (JSON)* button produces, so the
round trip needs no translation at either end:

.. code-block:: json

    {
      "datasetVersion": "measured-v1",
      "generated": "2026-08-17T09:00:00Z",
      "mergePolicy": "scanner-wins",
      "edits": {
        "sphere-vlf-seattle-1722211200000": {
          "id": "sphere-vlf-seattle-1722211200000",
          "disputed": true,
          "disputeNote": "sheet says 10:03, the raw files start 10:31",
          "provenance": "manual",
          "_editedAt": "2026-08-17T09:00:00Z"
        }
      }
    }
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

#: Fields a correction may set on an existing record: human judgement, which no adapter produces.
#: The dashboard's editor already writes only these; this is the check that the file on disk did
#: not come from somewhere with looser ideas.
HUMAN_FIELDS = frozenset({
    "disputed",
    "disputeNote",
    "processingConclusion",
    "processingResultUrl",
    "publishState",
})

#: Bookkeeping the dashboard attaches to every edit. Carried through, not treated as a field the
#: correction is trying to set.
_META_FIELDS = frozenset({"id", "provenance", "_editedAt", "_added", "recordKind"})


@dataclass
class Corrections:
    """Edits to existing records, plus records a person added by hand."""

    edits: dict[str, dict[str, Any]] = field(default_factory=dict)
    added: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    source: str = ""

    def __bool__(self) -> bool:
        return bool(self.edits or self.added)

    @property
    def summary(self) -> str:
        parts = []
        if self.edits:
            parts.append(f"{len(self.edits)} correction(s)")
        if self.added:
            parts.append(f"{len(self.added)} hand-entered record(s)")
        return ", ".join(parts) or "nothing to apply"


def load(path: Path) -> Corrections:
    """Read a patch. A missing file is not an error -- most publishes have no corrections."""
    result = Corrections(source=str(path))
    if not path.exists():
        return result

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        result.warnings.append(f"corrections at {path} could not be read: {error}")
        return result

    policy = raw.get("mergePolicy")
    if policy not in (None, "scanner-wins"):
        # The file declares its own policy. If it ever declares one we do not implement, say so
        # rather than applying ours to a patch built under different rules.
        result.warnings.append(
            f"corrections at {path} declare mergePolicy {policy!r}, which this build does not "
            f"implement; nothing was applied"
        )
        return result

    for record_id, edit in (raw.get("edits") or {}).items():
        if not isinstance(edit, dict):
            result.warnings.append(f"correction for {record_id!r} is not an object; it was skipped")
            continue
        if edit.get("_added"):
            result.added.append(dict(edit, id=record_id))
            continue

        offered = {k: v for k, v in edit.items() if k not in _META_FIELDS}
        refused = sorted(set(offered) - HUMAN_FIELDS)
        if refused:
            result.warnings.append(
                f"correction for {record_id!r} tried to set {', '.join(refused)}, which the record "
                f"gets from its source; the scanner wins, so those were not applied. Mark the "
                f"record disputed instead."
            )
        allowed = {k: v for k, v in offered.items() if k in HUMAN_FIELDS}
        if allowed:
            result.edits[record_id] = allowed

    return result


def apply_to(records: list[dict[str, Any]], corrections: Corrections) -> tuple[int, list[str]]:
    """Merge corrections into ``records`` in place. Returns how many applied, and what went wrong.

    A record that a correction names but the record set does not contain is reported rather than
    ignored: it usually means the record was regenerated with a different id, and a correction
    silently pointing at nothing is exactly the kind of quiet loss this project exists to avoid.
    """
    if not corrections.edits:
        return 0, []

    by_id = {record["id"]: record for record in records if "id" in record}
    applied, missing = 0, []
    for record_id, fields in corrections.edits.items():
        target = by_id.get(record_id)
        if target is None:
            missing.append(record_id)
            continue
        target.update(fields)
        # Say that a human touched this, without discarding which source the record came from.
        target["corrected"] = True
        applied += 1

    warnings = []
    if missing:
        shown = ", ".join(sorted(missing)[:5])
        more = f" (and {len(missing) - 5} more)" if len(missing) > 5 else ""
        warnings.append(
            f"{len(missing)} correction(s) name records that are not in the published set: "
            f"{shown}{more}"
        )
    return applied, warnings
