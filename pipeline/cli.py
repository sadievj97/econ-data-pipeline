"""Command-line entry point: python -m pipeline <command> (or .\\pipeline <command>)."""

import argparse
import sys
import textwrap
from datetime import date
from pathlib import Path

from . import __version__
from .cache import CacheIntegrityError, RawCache
from .config import Config, load_config
from .connectors import SourceError, Sources
from .net import DownloadError, make_client
from .paths import PROJECT_ROOT, SPECS_DIR
from .spec import SpecError, describe_spec, load_spec

SOURCE_PROBLEMS = (SourceError, DownloadError, CacheIntegrityError)


def open_sources(config: Config, pins: dict[str, str] | None = None) -> Sources:
    cache = RawCache(config.folder("raw_cache"), pins=pins)
    return Sources(cache, make_client(config.http.timeout_seconds), attempts=config.http.attempts,
                   manual_dir=config.folder("manual"))


def _pins_for(spec_path: Path, refresh: bool) -> dict[str, str] | None:
    from .build import lock_path, read_lock

    path = lock_path(spec_path)
    return read_lock(path) if path.exists() and not refresh else None


def cmd_check_setup(args: argparse.Namespace) -> int:
    from .setup_check import report

    text, ok = report()
    print(text)
    return 0 if ok else 1


def cmd_check_spec(args: argparse.Namespace) -> int:
    try:
        spec = load_spec(args.spec)
    except SpecError as exc:
        print(exc)
        return 1
    print(describe_spec(spec))
    print()
    print("The spec passes all checks.")
    if not spec.confirmed:
        print("When you are happy with it, change 'confirmed: false' to 'confirmed: true'.")
    return 0


def cmd_search(args: argparse.Namespace) -> int:
    from .catalog import search

    sources = open_sources(load_config())
    try:
        hits = search(sources.get(args.source).catalog().values(), args.query, args.limit)
    except SOURCE_PROBLEMS as exc:
        print(exc)
        return 1
    finally:
        sources.close()
    if not hits:
        print(f"No {args.source} indicators match '{args.query}'. Try other words.")
        return 1
    for h in hits:
        basis = h.unit.price_basis or "price basis not stated"
        year = f", base year {h.unit.base_year}" if h.unit.base_year else ""
        print(f"{h.code}  {h.name}")
        print(f"    unit: {h.unit.text or 'not stated'} ({basis}{year})")
        if h.definition and h.definition != h.name:
            print(textwrap.fill(h.definition[:400], width=100, initial_indent="    ", subsequent_indent="    "))
        print()
    return 0


def cmd_describe(args: argparse.Namespace) -> int:
    import json

    sources = open_sources(load_config())
    try:
        connector = sources.get(args.source)
        if not hasattr(connector, "describe"):
            info = connector.info(args.code, args.dataset)
            print(f"{info.code}  {info.name}\n    unit: {info.unit.text or 'not stated'}\n    {info.definition}")
            return 0
        result = connector.describe(args.code, args.dataset, args.country.upper())
    except SOURCE_PROBLEMS as exc:
        print(exc)
        return 1
    finally:
        sources.close()
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


def cmd_fetch(args: argparse.Namespace) -> int:
    from .fetching import coverage, fetch_spec

    try:
        spec = load_spec(args.spec)
    except SpecError as exc:
        print(exc)
        return 1
    config = load_config()
    pins = _pins_for(Path(args.spec), args.refresh)
    sources = open_sources(config, pins)
    print(f"Fetching {spec.name}: {len(spec.countries)} countries, {len(spec.variables)} variables.")
    if args.refresh:
        print("Downloading everything again (--refresh).")
    else:
        print("Using the raw files pinned in the spec's lock file." if pins else "Files already in data/raw/ are reused.")
    try:
        reports = fetch_spec(spec, sources, refresh=args.refresh)
    finally:
        sources.close()

    errors = warnings = skipped = 0
    for r in reports:
        print()
        print(f"{r.variable}  {r.ref.source} {r.ref.code}")
        if r.skipped:
            skipped += 1
            print(f"    skipped: {r.skipped}")
            continue
        if r.series is not None:
            cov = coverage(r.series, spec)
            print(f"    values in {spec.years.start}-{spec.years.end}: {cov.with_values} of {cov.expected}")
            for iso3, years in cov.missing.items():
                print(f"    missing for {iso3}: {_year_ranges(years)}")
            print(f"    source unit: {r.series.info.unit.text or 'not stated'}")
            print(f"    raw file: {r.series.raw[0].relative_file()}"
                  + (f" (+{len(r.series.raw) - 1} more)" if len(r.series.raw) > 1 else ""))
        for e in r.errors:
            errors += 1
            print(f"    PROBLEM: {e}")
        for w in r.warnings:
            warnings += 1
            print(f"    check: {w}")
    print()
    print(f"Done: {len(reports) - skipped} series fetched, {errors} problem(s), {warnings} thing(s) to check, "
          f"{skipped} skipped.")
    if errors:
        print("Fix each PROBLEM in the spec (or choose another code) before building.")
    return 1 if errors else 0


def _year_ranges(years: list[int]) -> str:
    parts, start = [], None
    for i, y in enumerate(years):
        if start is None:
            start = y
        if i + 1 == len(years) or years[i + 1] != y + 1:
            parts.append(str(start) if start == y else f"{start}-{y}")
            start = None
    return ", ".join(parts)


def _free_path(path: Path) -> Path:
    if not path.exists():
        return path
    n = 2
    while path.with_name(f"{path.stem}_{n}{path.suffix}").exists():
        n += 1
    return path.with_name(f"{path.stem}_{n}{path.suffix}")


def cmd_request(args: argparse.Namespace) -> int:
    import anthropic

    from .drafting import DraftError, draft_spec, draft_yaml, review_markdown
    from .envfile import MissingKeyError, get_key

    config = load_config()
    try:
        key = get_key("ANTHROPIC_API_KEY")
    except MissingKeyError as exc:
        print(exc)
        return 1

    sources = open_sources(config)
    try:
        print("Loading the WDI and PWT 11.0 catalogs (downloaded once, then reused from data/raw/);")
        print("other catalogs load the first time Claude searches them...")
        for name in ("wdi", "pwt"):
            sources.get(name).catalog()
        print(f"Asking {config.models.spec_drafting} to draft a spec:")
        client = anthropic.Anthropic(api_key=key, max_retries=3)
        result = draft_spec(args.text, client=client, config=config, sources=sources)
    except anthropic.AuthenticationError:
        print("The Anthropic API did not accept the key on the ANTHROPIC_API_KEY line of .env. "
              "Check it was pasted completely, or create a new key.")
        return 1
    except anthropic.PermissionDeniedError:
        print("The Anthropic API key in .env is not allowed to use this model. Check your account settings.")
        return 1
    except anthropic.RateLimitError:
        print("The Anthropic API is busy or your account hit a usage limit. Wait a minute and try again.")
        return 1
    except anthropic.APIConnectionError:
        print("Could not reach the Anthropic API. Check the internet connection and try again.")
        return 1
    except anthropic.APIStatusError as exc:
        print(f"The Anthropic API returned an error (HTTP {exc.status_code}): {exc.message}")
        return 1
    except DraftError as exc:
        print(exc)
        return 1
    except SOURCE_PROBLEMS as exc:
        print(exc)
        return 1
    finally:
        sources.close()

    spec_path = _free_path(Path(args.out) if args.out else SPECS_DIR / f"{result.spec['name']}.yaml")
    review_path = spec_path.with_name(f"{spec_path.stem}.review.md")
    today = date.today().isoformat()
    spec_path.parent.mkdir(parents=True, exist_ok=True)
    spec_path.write_text(draft_yaml(result, review_path.name, today), encoding="utf-8")
    review_path.write_text(review_markdown(result, spec_path.name, today), encoding="utf-8")

    print()
    print(f"Draft spec: {spec_path}")
    print(f"Review notes (definitions and alternatives): {review_path}")
    print()
    for v in result.spec["variables"]:
        codes = ", ".join(f"{s['source']} {s['code']}" for s in v["sources"])
        print(f"  {v['name']}: {codes}")
    if result.to_fill:
        print()
        print("Fill in by hand (marked FILL_ME in the file):")
        for item in result.to_fill:
            print(f"  - {item}")
    if result.open_questions:
        print()
        print("Questions for you:")
        for q in result.open_questions:
            print(textwrap.fill(q, width=100, initial_indent="  - ", subsequent_indent="    "))
    print()
    shown = spec_path.relative_to(PROJECT_ROOT) if spec_path.is_relative_to(PROJECT_ROOT) else spec_path
    print(f"Next: review the files, then run  .\\pipeline check-spec {shown}")
    t = result.tokens
    read = t.get("input", 0) + t.get("cache_write", 0) + t.get("cache_read", 0)
    print(f"(Claude read {read:,} tokens, {t.get('cache_read', 0):,} of them from its cache, "
          f"and wrote {t.get('output', 0):,}.)")
    return 0


def cmd_build(args: argparse.Namespace) -> int:
    try:
        spec = load_spec(args.spec)
    except SpecError as exc:
        print(exc)
        return 1
    if not spec.confirmed:
        print(
            f"{Path(args.spec).name} is still a draft, so nothing was built.\n"
            "Review it, change 'confirmed: false' to 'confirmed: true', then run build again."
        )
        return 1
    from .build import run_build
    from .events import load_events

    config = load_config()
    spec_path = Path(args.spec)
    pins = _pins_for(spec_path, args.refresh)
    sources = open_sources(config, pins)
    print(f"Building {spec.name}: {len(spec.countries)} countries, {len(spec.variables)} variables, "
          f"{spec.years.start}-{spec.years.end}.")
    if pins:
        print("Using the raw files pinned in the spec's lock file.")
    try:
        result = run_build(spec_path, spec, sources, config, load_events(), refresh=args.refresh, progress=print)
    finally:
        sources.close()

    report = result.report
    known_ok = sum(k.matched for k in report.known)
    print()
    print(f"Errors: {len(report.errors)}   Flags to look at: {len(report.flags)}   "
          f"Known values matched: {known_ok} of {len(report.known)}   Missing country-years: {len(report.missing)}")
    for f in report.errors[:10]:
        where = " ".join(x for x in (f.variable, f.iso3) if x)
        print(f"  ERROR {f.check} {where}: {f.message}")
    if len(report.errors) > 10:
        print(f"  ...and {len(report.errors) - 10} more")
    print()
    for path in result.written:
        shown = path.relative_to(PROJECT_ROOT) if path.is_relative_to(PROJECT_ROOT) else path
        print(f"Wrote {shown}")
    for path in result.removed:
        print(f"Removed {path.name} left by an earlier build (it no longer matches a passing validation)")
    if result.lock_changed and result.lock_file is not None:
        print(f"Pinned the raw files used in {result.lock_file.name} (later builds reuse exactly these).")
    if report.errors:
        print("The build FAILED validation, so no panel files were written. Fix the errors above "
              "(details in validation_report.md).")
        return 1
    shown = result.out_dir.relative_to(PROJECT_ROOT) if result.out_dir.is_relative_to(PROJECT_ROOT) else result.out_dir
    print(f"Validation passed. In R, run {shown}\\load_and_check.R to load the panel and recheck it"
          + (" (or load_and_check.do in Stata)." if config.outputs.stata else "."))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pipeline",
        description="Build documented country-year panels from official statistics.",
    )
    parser.add_argument("--version", action="version", version=f"pipeline {__version__}")
    sub = parser.add_subparsers(dest="command", required=True, metavar="command")

    p = sub.add_parser("check-setup", help="show what is installed and whether keys are in place (never shows keys)")
    p.set_defaults(func=cmd_check_setup)

    p = sub.add_parser("request", help="turn a plain-English request into a draft spec (uses Claude)")
    p.add_argument("text", help="your request, in quotes")
    p.add_argument("--out", help="where to save the draft spec (default: specs/<name>.yaml)")
    p.set_defaults(func=cmd_request)

    p = sub.add_parser("search", help="search a source's indicator catalog")
    p.add_argument("source", choices=["wdi", "pwt", "ilostat", "imf", "faostat", "nso_uzb"])
    p.add_argument("query", help="words to look for, in quotes")
    p.add_argument("--limit", type=int, default=10)
    p.set_defaults(func=cmd_search)

    p = sub.add_parser("describe", help="list the filter choices an indicator offers for one country")
    p.add_argument("source", choices=["wdi", "pwt", "ilostat", "imf", "faostat", "nso_uzb"])
    p.add_argument("code", help="indicator code (for FAOSTAT: an item code, or a dataset code to list its items)")
    p.add_argument("--dataset", help="IMF dataset such as IMF.RES,WEO, or FAOSTAT dataset such as CS")
    p.add_argument("--country", default="UZB", help="ISO3 country code (default UZB)")
    p.set_defaults(func=cmd_describe)

    p = sub.add_parser("check-spec", help="check a spec file and summarize it")
    p.add_argument("spec", help="path to a spec .yaml file")
    p.set_defaults(func=cmd_check_spec)

    p = sub.add_parser("fetch", help="download (or reuse cached) raw data for a spec and check units")
    p.add_argument("spec", help="path to a spec .yaml file")
    p.add_argument("--refresh", action="store_true", help="download again even if cached files exist")
    p.set_defaults(func=cmd_fetch)

    p = sub.add_parser("build", help="fetch, harmonize, validate and write outputs for a confirmed spec")
    p.add_argument("spec", help="path to a confirmed spec .yaml file")
    p.add_argument("--refresh", action="store_true", help="download again instead of using the pinned raw files")
    p.set_defaults(func=cmd_build)
    return parser


def main(argv: list[str] | None = None) -> int:
    # Country names and source text can contain characters the Windows console
    # cannot show; replace them rather than crash.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass
    args = build_parser().parse_args(argv)
    return args.func(args)
