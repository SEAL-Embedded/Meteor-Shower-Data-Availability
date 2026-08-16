"""Command line entry points: publish, serve, check."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .config import DEFAULT_CONFIG_NAME, Config, ConfigError
from .core.snapshot import write_campaign, write_snapshot
from .ingest.base import registered_adapters
from .models import SourceStatus, iso
from .store import Store


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return 2

    try:
        config = Config.load(getattr(args, "config", DEFAULT_CONFIG_NAME))
    except ConfigError as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        return 2

    if args.command == "publish":
        return _publish(config, strict=args.strict)
    if args.command == "check":
        return _check(config)
    if args.command == "serve":
        return _serve(config, args)
    return 2


def _parser() -> argparse.ArgumentParser:
    # --config is attached to the top level and to every subcommand, so both
    # `availability --config x check` and `availability check --config x` work. Accepting only one
    # order means half the documented commands are wrong and the error message does not say why.
    # The default is SUPPRESS rather than a value: an unset subparser default would otherwise
    # overwrite a --config given before the command.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--config",
        default=argparse.SUPPRESS,
        metavar="PATH",
        help=(
            f"path to the TOML configuration (default: {DEFAULT_CONFIG_NAME}); "
            f"may be given before or after the command"
        ),
    )

    parser = argparse.ArgumentParser(
        prog="availability",
        parents=[common],
        description="Publish and serve the instrument availability record.",
    )
    subcommands = parser.add_subparsers(dest="command")

    publish = subcommands.add_parser(
        "publish", parents=[common], help="write JSON snapshots for the static front end"
    )
    publish.add_argument(
        "--strict",
        action="store_true",
        help="fail if any source reported an error, instead of publishing what did work",
    )

    subcommands.add_parser(
        "check",
        parents=[common],
        help="run every source and report what it found, without writing anything",
    )

    serve = subcommands.add_parser("serve", parents=[common], help="run the HTTP API")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument(
        "--refresh-seconds",
        type=float,
        default=None,
        help="how long a built record is reused before sources are re-read",
    )
    serve.add_argument(
        "--with-web",
        metavar="DIRECTORY",
        default=None,
        help="also serve a static front end from this directory, for local development",
    )
    serve.add_argument("--debug", action="store_true")
    return parser


def _publish(config: Config, *, strict: bool) -> int:
    store = Store.build(config)
    _report(store)

    failures = [source for source in store.sources if source.status is SourceStatus.ERROR]
    if strict and failures:
        print(
            f"\nrefusing to publish: {len(failures)} source(s) failed and --strict was given",
            file=sys.stderr,
        )
        return 1

    output_dir = config.resolve(config.output_dir)
    written = write_snapshot(store, output_dir)
    print(f"\nwrote {len(written)} file(s) to {output_dir}:")
    for path in written:
        print(f"  {path.name}")

    if config.campaign.enabled:
        target = write_campaign(store, config)
        print(f"\nwrote the dashboard dataset to {target}")
    return 0


def _check(config: Config) -> int:
    store = Store.build(config)
    _report(store)
    print(f"\nregistered adapters: {', '.join(registered_adapters())}")
    failures = [source for source in store.sources if source.status is SourceStatus.ERROR]
    return 1 if failures else 0


def _report(store: Store) -> None:
    window = store.range
    print(f"generated at {iso(store.generated_at)}")
    print(f"range        {iso(window.start)} .. {iso(window.end)}" if window else "range        —")
    print(f"instruments  {len(store.instruments)}")
    print(f"coverage     {len(store.coverage)} interval(s), {len(store.segments)} segment(s)")
    print(f"events       {len(store.events)}")

    if store.instruments:
        print("\ninstruments:")
        for instrument in store.instruments:
            known = instrument.known_range
            span = f"{iso(known.start)} .. {iso(known.end)}" if known else "not characterised"
            count = sum(1 for r in store.coverage if r.instrument_id == instrument.id)
            print(f"  {instrument.id:<28} {count:>5} interval(s)  {span}")

    if store.sources:
        print("\nsources:")
        for source in store.sources:
            print(f"  {source.id:<28} {source.status.value}")
            if source.detail:
                print(f"      {source.detail}")

    if store.event_coverage:
        counts: dict[str, int] = {}
        for record in store.event_coverage:
            counts[record.verdict.value] = counts.get(record.verdict.value, 0) + 1
        summary = ", ".join(f"{verdict}: {count}" for verdict, count in sorted(counts.items()))
        print(f"\nevent verdicts: {summary}")

    if store.warnings:
        print(f"\nwarnings ({len(store.warnings)}):")
        for warning in store.warnings:
            print(f"  {warning}")


def _serve(config: Config, args: argparse.Namespace) -> int:
    try:
        from .app import create_app
    except ImportError:
        print(
            "serving needs Flask; install it with: python -m pip install -e 'backend[api]'",
            file=sys.stderr,
        )
        return 2

    web_dir = Path(args.with_web).resolve() if args.with_web else None
    if web_dir is not None and not web_dir.is_dir():
        print(f"no such directory: {web_dir}", file=sys.stderr)
        return 2

    app = create_app(config, refresh_s=args.refresh_seconds, web_dir=web_dir)
    app.run(host=args.host, port=args.port, debug=args.debug)
    return 0
