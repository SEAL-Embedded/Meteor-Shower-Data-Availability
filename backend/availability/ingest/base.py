"""Ingest adapter interface and registry.

An adapter turns one external thing -- a directory of recordings, a catalogue export, an HTTP API --
into coverage intervals or events. Adapters are registered by name and selected in configuration.

**Adapters do not raise on bad input.** A missing directory or an unreachable catalogue marks its
source as failed and returns nothing; it does not abort the run. One broken source must not take the
whole published record offline, and a source that fails silently is worse than one that says so, so
every failure lands in ``Source.status`` and ``Source.detail`` where the front end can show it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Type

from ..config import Config, SourceConfig
from ..models import (
    CoverageInterval,
    Event,
    Source,
    SourceKind,
    SourceStatus,
    Span,
    now,
)


@dataclass
class CoverageResult:
    source: Source
    intervals: list[CoverageInterval] = field(default_factory=list)
    known_ranges: dict[str, Span] = field(default_factory=dict)
    """Per instrument, the period this adapter actually examined.

    Distinct from the union of the intervals: an adapter that scanned a whole month and found
    two days of recordings has characterised the month, and the other twenty-nine days are
    genuine downtime rather than unexamined time.
    """


@dataclass
class EventResult:
    source: Source
    events: list[Event] = field(default_factory=list)


class Adapter:
    """Base class for ingest adapters."""

    #: Which sort of source this adapter can serve.
    kind: SourceKind = SourceKind.COVERAGE

    def __init__(self, config: Config, source_config: SourceConfig) -> None:
        self.config = config
        self.source_config = source_config

    # -- helpers available to subclasses -------------------------------------------------

    def option(self, name: str, default=None):
        return self.source_config.options.get(name, default)

    def required_option(self, name: str):
        if name not in self.source_config.options:
            raise AdapterError(
                f"source {self.source_config.id!r} needs a {name!r} setting for the "
                f"{self.source_config.adapter!r} adapter"
            )
        return self.source_config.options[name]

    def resolve_directory(self, name: str = "path"):
        """Resolve a configured directory, which may be a list of candidates.

        The data drive is not guaranteed to keep the same letter between machines or reboots, so
        a setting may name several places and the first that exists wins. Returns the directory
        and every path tried, because "no such directory: D:/nimbustrace" is a much less useful
        thing to read than the full list of where it looked.
        """
        raw = self.required_option(name)
        candidates = [raw] if isinstance(raw, (str, Path)) else list(raw)
        tried: list[str] = []
        for candidate in candidates:
            resolved = self.config.resolve(candidate)
            tried.append(str(resolved))
            if resolved.is_dir():
                return resolved, tried
        return None, tried

    def describe(self, status: SourceStatus = SourceStatus.OK, detail: str | None = None) -> Source:
        return Source(
            id=self.source_config.id,
            name=self.source_config.name,
            kind=self.source_config.kind,
            url=self.source_config.url,
            attribution=self.source_config.attribution,
            fetched_at=now(),
            status=status,
            detail=detail,
        )

    # -- interface -----------------------------------------------------------------------

    def fetch(self) -> CoverageResult | EventResult:
        raise NotImplementedError


class AdapterError(Exception):
    """A problem an adapter can describe to the reader rather than crash on."""


_REGISTRY: dict[str, Type[Adapter]] = {}


def register(name: str) -> Callable[[Type[Adapter]], Type[Adapter]]:
    def decorate(cls: Type[Adapter]) -> Type[Adapter]:
        if name in _REGISTRY:
            raise RuntimeError(f"adapter {name!r} is already registered")
        _REGISTRY[name] = cls
        cls.adapter_name = name  # type: ignore[attr-defined]
        return cls

    return decorate


def get_adapter(name: str) -> Type[Adapter]:
    try:
        return _REGISTRY[name]
    except KeyError:
        raise AdapterError(
            f"unknown adapter {name!r}; available adapters are {sorted(_REGISTRY)}"
        ) from None


def registered_adapters() -> list[str]:
    return sorted(_REGISTRY)


def empty_result(
    source_config: SourceConfig,
    status: SourceStatus,
    detail: str,
) -> CoverageResult | EventResult:
    """A result carrying only an explanation, for a source that produced nothing."""
    source = Source(
        id=source_config.id,
        name=source_config.name,
        kind=source_config.kind,
        url=source_config.url,
        attribution=source_config.attribution,
        fetched_at=now(),
        status=status,
        detail=detail,
    )
    if source_config.kind is SourceKind.EVENT:
        return EventResult(source=source)
    return CoverageResult(source=source)


def span_of(intervals: Iterable[CoverageInterval]) -> Span | None:
    records = list(intervals)
    if not records:
        return None
    return Span(min(r.start for r in records), max(r.end for r in records))
