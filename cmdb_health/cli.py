"""Command-line entry point.

    python -m cmdb_health --demo
    python -m cmdb_health --live --out reports
    python -m cmdb_health --demo --fail-under 90     (exit code 1 if the score is lower)
"""
from __future__ import annotations

import argparse
import os
import sys
import webbrowser
from pathlib import Path

from . import __version__
from .checks import run_checks
from .config import load_rules
from .report import top_fixes, write_all
from .scoring import build_scorecard


def _load_dotenv(path: Path = Path(".env")) -> None:
    """Minimal .env reader so credentials can live in a git-ignored file."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="cmdb-health",
        description="Score the health of a ServiceNow CMDB and list exactly what to fix.",
    )
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument("--demo", action="store_true", help="use built-in sample data (no instance needed)")
    mode.add_argument("--live", action="store_true", help="read from ServiceNow using SN_* environment variables")
    p.add_argument("--classes", help="comma-separated CI classes to scan in live mode")
    p.add_argument("--rules", help="JSON file that overrides the default rules")
    p.add_argument("--out", default="reports", help="output folder (default: reports)")
    p.add_argument("--seed", type=int, default=42, help="random seed for demo data")
    p.add_argument("--fail-under", type=float, help="exit with code 1 if the score is below this value")
    p.add_argument("--open", action="store_true", help="open the HTML report when done")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    rules = load_rules(args.rules)

    if args.demo:
        from .sources.demo import build_demo_dataset

        data = build_demo_dataset(seed=args.seed)
    else:
        _load_dotenv()
        from .sources.servicenow import ServiceNowClient, ServiceNowError

        try:
            client = ServiceNowClient.from_env()
            classes = [c.strip() for c in args.classes.split(",")] if args.classes else None
            print(f"Reading CMDB from {client.host} ...")
            data = client.load(classes)
        except ServiceNowError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 2

    checks = run_checks(data, rules)
    card = build_scorecard(data, checks)
    paths = write_all(card, data, Path(args.out))

    print()
    print(f"  CMDB health: {card.score:.1f} / 100  (grade {card.grade})")
    print(f"  {card.affected_cis} of {card.active_cis} active CIs have at least one issue")
    print()
    for c in checks:
        print(f"  {c.title:<18} {c.pass_rate * 100:5.1f}%   {c.affected:>4} affected of {c.applicable}")
    fixes = top_fixes(card, 3)
    if fixes:
        print("\n  Fix first:")
        for f in fixes:
            print(f"   - {f['label']} ({f['count']})")
    print()
    for kind, path in paths.items():
        print(f"  {kind.upper():<5} {path}")

    if args.open:
        webbrowser.open(paths["html"].resolve().as_uri())
    if args.fail_under is not None and card.score < args.fail_under:
        print(f"\n  Score {card.score:.1f} is below the threshold of {args.fail_under:g}.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
