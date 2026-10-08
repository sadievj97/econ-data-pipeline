"""Command-line entry point: python -m pipeline <command> (or .\\pipeline <command>)."""

import argparse
import sys
from pathlib import Path

from . import __version__
from .spec import SpecError, describe_spec, load_spec


def cmd_check_setup(args: argparse.Namespace) -> int:
    from .setup_check import report

    text, ok = report()
    print(text)
    return 0 if ok else 1


def cmd_request(args: argparse.Namespace) -> int:
    print(
        "The request step arrives in Phase 2: it needs the WDI and PWT indicator catalogs to search.\n"
        "For now, specs/example_uzb_kaz.yaml shows what a spec looks like."
    )
    return 1


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
    print("The spec is valid and confirmed. Fetching data arrives in Phase 2, so nothing was built yet.")
    return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pipeline",
        description="Build documented country-year panels from official statistics.",
    )
    parser.add_argument("--version", action="version", version=f"pipeline {__version__}")
    sub = parser.add_subparsers(dest="command", required=True, metavar="command")

    p = sub.add_parser("check-setup", help="show what is installed and whether keys are in place (never shows keys)")
    p.set_defaults(func=cmd_check_setup)

    p = sub.add_parser("request", help="turn a plain-English request into a draft spec")
    p.add_argument("text", help="your request, in quotes")
    p.add_argument("--out", help="where to save the draft spec")
    p.set_defaults(func=cmd_request)

    p = sub.add_parser("check-spec", help="check a spec file and summarize it")
    p.add_argument("spec", help="path to a spec .yaml file")
    p.set_defaults(func=cmd_check_spec)

    p = sub.add_parser("build", help="fetch, validate, harmonize and write outputs for a confirmed spec")
    p.add_argument("spec", help="path to a confirmed spec .yaml file")
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
