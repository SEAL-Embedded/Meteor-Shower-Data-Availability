"""Configuration loading.

Instruments and data sources are declared in TOML, not in code. Adding an instrument is a config
entry; adding a *kind* of data source is a config entry plus one ingest adapter.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .models import Band, Instrument, InstrumentKind, Site, SourceKind

DEFAULT_CONFIG_NAME = "config.toml"


class ConfigError(Exception):
    """Raised when the configuration cannot be understood. Never guessed around."""


@dataclass
class SourceConfig:
    id: str
    adapter: str
    kind: SourceKind
    name: str
    enabled: bool = True
    url: str | None = None
    attribution: str | None = None
    options: dict[str, Any] = field(default_factory=dict)


@dataclass
class CampaignConfig:
    """Settings for the dashboard dataset export.

    The id and vocabulary mappings live here rather than in code, so the front end's naming can
    change without the record's naming having to.
    """

    enabled: bool = False
    path: Path = Path("data/campaign.json")
    dataset_version: str = "measured-v1"
    scanner_version: str = "none — coverage is transcribed, not scanned"
    site_altitude_m: float | None = None
    instrument_ids: dict[str, str] = field(default_factory=dict)
    provenance: dict[str, str] = field(default_factory=dict)
    event_sources: dict[str, str] = field(default_factory=dict)

    def export_id(self, instrument_id: str) -> str:
        return self.instrument_ids.get(instrument_id, instrument_id)

    def provenance_for(self, source_id: str | None) -> str:
        return self.provenance.get(source_id or "", "measured")

    def event_source_for(self, source_id: str | None) -> str:
        return self.event_sources.get(source_id or "", source_id or "unknown")


@dataclass
class Config:
    title: str
    instruments: list[Instrument]
    sources: list[SourceConfig]
    base_dir: Path
    output_dir: Path
    allowed_origins: list[str] = field(default_factory=list)
    campaign: CampaignConfig = field(default_factory=CampaignConfig)
    events_within_coverage: bool = False
    """Keep only events falling inside some instrument's characterised period.

    Catalogues return whole years. An event from a month nobody was observing can only ever be
    reported as ``unknown``, and in quantity those bury the events the record can actually answer
    for. What gets dropped is counted and reported, never silently.
    """

    def instrument(self, instrument_id: str) -> Instrument | None:
        return next((i for i in self.instruments if i.id == instrument_id), None)

    def resolve(self, value: str | Path) -> Path:
        """Resolve a configured path relative to the config file's directory."""
        candidate = Path(value)
        return candidate if candidate.is_absolute() else (self.base_dir / candidate)

    @classmethod
    def load(cls, path: str | Path) -> "Config":
        config_path = Path(path).expanduser().resolve()
        if not config_path.is_file():
            raise ConfigError(f"no configuration file at {config_path}")
        try:
            raw = tomllib.loads(config_path.read_text(encoding="utf-8"))
        except tomllib.TOMLDecodeError as exc:
            raise ConfigError(f"{config_path} is not valid TOML: {exc}") from exc
        return cls.from_dict(raw, base_dir=config_path.parent)

    @classmethod
    def from_dict(cls, raw: dict[str, Any], *, base_dir: Path) -> "Config":
        site_section = raw.get("site", {})
        output_section = raw.get("output", {})

        instruments = [_instrument(entry) for entry in raw.get("instruments", [])]
        _reject_duplicates(instrument.id for instrument in instruments)

        sources = [_source(entry) for entry in raw.get("sources", [])]
        _reject_duplicates(source.id for source in sources)

        return cls(
            title=site_section.get("title", "Meteor Shower Data Availability"),
            instruments=instruments,
            sources=sources,
            base_dir=base_dir,
            output_dir=Path(output_section.get("directory", "web/data")),
            allowed_origins=list(raw.get("api", {}).get("allowed_origins", [])),
            campaign=_campaign(raw.get("campaign", {})),
            events_within_coverage=bool(
                raw.get("events", {}).get("within_coverage", False)
            ),
        )


def _instrument(entry: dict[str, Any]) -> Instrument:
    try:
        kind = InstrumentKind(entry.get("kind", "other"))
    except ValueError as exc:
        raise ConfigError(
            f"instrument {entry.get('id')!r} has unknown kind {entry.get('kind')!r}; "
            f"expected one of {[k.value for k in InstrumentKind]}"
        ) from exc

    for required in ("id", "name"):
        if not entry.get(required):
            raise ConfigError(f"instrument entry is missing {required!r}: {entry!r}")

    site_entry = entry.get("site")
    band_entry = entry.get("band_hz")
    return Instrument(
        id=entry["id"],
        name=entry["name"],
        kind=kind,
        system=entry.get("system", entry["id"]),
        site=(
            Site(
                id=site_entry.get("id", ""),
                name=site_entry.get("name", ""),
                latitude=site_entry.get("latitude"),
                longitude=site_entry.get("longitude"),
            )
            if site_entry
            else None
        ),
        band_hz=(
            Band(low=float(band_entry["low"]), high=float(band_entry["high"]))
            if band_entry
            else None
        ),
        active=bool(entry.get("active", True)),
    )


_SOURCE_RESERVED = {"id", "adapter", "kind", "name", "enabled", "url", "attribution"}


def _source(entry: dict[str, Any]) -> SourceConfig:
    for required in ("id", "adapter", "kind"):
        if not entry.get(required):
            raise ConfigError(f"source entry is missing {required!r}: {entry!r}")
    try:
        kind = SourceKind(entry["kind"])
    except ValueError as exc:
        raise ConfigError(
            f"source {entry['id']!r} has unknown kind {entry['kind']!r}; "
            f"expected one of {[k.value for k in SourceKind]}"
        ) from exc

    return SourceConfig(
        id=entry["id"],
        adapter=entry["adapter"],
        kind=kind,
        name=entry.get("name", entry["id"]),
        enabled=bool(entry.get("enabled", True)),
        url=entry.get("url"),
        attribution=entry.get("attribution"),
        options={key: value for key, value in entry.items() if key not in _SOURCE_RESERVED},
    )


def _campaign(entry: dict[str, Any]) -> CampaignConfig:
    defaults = CampaignConfig()
    return CampaignConfig(
        enabled=bool(entry.get("enabled", False)),
        path=Path(entry.get("path", defaults.path)),
        dataset_version=entry.get("dataset_version", defaults.dataset_version),
        scanner_version=entry.get("scanner_version", defaults.scanner_version),
        site_altitude_m=entry.get("site_altitude_m"),
        instrument_ids=dict(entry.get("instrument_ids", {})),
        provenance=dict(entry.get("provenance", {})),
        event_sources=dict(entry.get("event_sources", {})),
    )


def _reject_duplicates(ids) -> None:
    seen: set[str] = set()
    for identifier in ids:
        if identifier in seen:
            raise ConfigError(f"duplicate id {identifier!r} in configuration")
        seen.add(identifier)
