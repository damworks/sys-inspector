#!/usr/bin/env python3
"""sys-inspector CLI: python main.py --project my_project --env production"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from core.checks import FAIL, OK, REGISTRY, WARN
from core.config import ConfigError
from core.engine import Engine
from core.results_store import ResultsStore, ResultsStoreError

ICONS = {OK: "✔", WARN: "⚠", FAIL: "✘"}


def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Diagnostics and health checks via SSH and database.")
    p.add_argument("--project", required=True, help="file name in config/projects/<name>.yaml")
    p.add_argument("--env", required=True, help="environment (e.g. staging, production)")
    p.add_argument("--only", action="append", metavar="TYPE|NAME",
                   help=f"run only checks of this type/name (repeatable). Types: {', '.join(REGISTRY)}")
    p.add_argument("--format", choices=["text", "json"], default="text")
    p.add_argument("--config-dir", type=Path, help="configuration directory (default: ./config)")
    p.add_argument("--env-file", type=Path, help=".env file (default: ./.env)")
    p.add_argument("--no-save-results", action="store_true",
                   help="do not persist results to the results database, even if RESULTS_DB_HOST is set")
    return p.parse_args(argv)


def print_text(project: str, env: str, results) -> None:
    print(f"== {project} / {env} ==")
    for r in results:
        print(f"\n{ICONS[r.status]} [{r.status}] {r.name} ({r.type}) - {r.summary}")
        for line in r.details:
            print(f"    {line}")
    counts = {s: sum(r.status == s for r in results) for s in (OK, WARN, FAIL)}
    print(f"\nTotal: {counts[OK]} OK, {counts[WARN]} WARN, {counts[FAIL]} FAIL")


def main(argv=None) -> int:
    args = parse_args(argv)
    try:
        engine = Engine(args.project, args.env, args.config_dir, args.env_file, args.only)
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2
    started_at = datetime.now(timezone.utc)
    results = engine.run()
    finished_at = datetime.now(timezone.utc)
    if args.format == "json":
        print(json.dumps([asdict(r) for r in results], indent=2, ensure_ascii=False))
    else:
        print_text(args.project, args.env, results)
    if not args.no_save_results:
        store = ResultsStore.from_env()
        if store:
            try:
                with store:
                    run_id = store.save(args.project, args.env, results, started_at, finished_at)
                print(f"Results saved to results DB (run #{run_id})", file=sys.stderr)
            except ResultsStoreError as exc:
                print(f"Warning: could not save results: {exc}", file=sys.stderr)
    return 1 if any(r.status == FAIL for r in results) else 0


if __name__ == "__main__":
    sys.exit(main())
