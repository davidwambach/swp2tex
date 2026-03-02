from __future__ import annotations

import argparse
from pathlib import Path
import sys

from .core import RunOptions, run_workflow


def _prompt_yes_no(message: str) -> bool:
    print(message)
    while True:
        ans = input("[y/n]: ").strip().lower()
        if ans in {"y", "yes"}:
            return True
        if ans in {"n", "no"}:
            return False


def _print_report(report) -> int:
    print(f"Build status: {report.build_status}")
    if report.normalized_tex_path:
        print(f"Normalized file: {report.normalized_tex_path}")
    if report.syntax_fixes:
        print("Syntax fixes:")
        for fix in report.syntax_fixes:
            print(f"  - {fix}")
    if report.export_path:
        print(f"Export zip: {report.export_path}")
    if report.warnings:
        print("Warnings:")
        for warn in report.warnings:
            print(f"  - {warn}")
    if report.errors:
        print("Errors:")
        for err in report.errors:
            print(f"  - {err}")
    return 1 if report.error_codes else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="swp2tex-bib")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="Run bibliography check and build.")
    run.add_argument("--main", required=True, type=Path, help="Main .ltx/.tex file.")
    run.add_argument(
        "--project-dir",
        required=True,
        type=Path,
        help="Directory containing graphics/bib/resources.",
    )
    run.add_argument(
        "--bib-file",
        type=Path,
        default=None,
        help="Optional .bib file to copy into project directory if required bibliography is missing.",
    )
    run.add_argument("--non-interactive", action="store_true")
    run.add_argument("--log", type=Path, default=None)
    run.add_argument(
        "--export-mode",
        choices=["none", "overleaf", "arxiv"],
        default="none",
        help="Export mode after successful build.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command != "run":
        parser.error("Unknown command.")

    options = RunOptions(
        main_file=args.main,
        project_dir=args.project_dir,
        interactive=not args.non_interactive,
        log_path=args.log,
        bib_file=args.bib_file,
        export_mode=None if args.export_mode == "none" else args.export_mode,
    )
    report = run_workflow(
        options=options,
        prompt_yes_no=None if args.non_interactive else _prompt_yes_no,
    )
    return _print_report(report)


if __name__ == "__main__":
    sys.exit(main())
