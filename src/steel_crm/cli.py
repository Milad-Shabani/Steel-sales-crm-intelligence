"""Command line: `python -m steel_crm.cli generate-data`, `... run` and `... bpmn`."""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

from .datagen.generator import generate_export
from .pipeline import run
from .processes.bpmn import write_bpmn
from .processes.definitions import all_processes


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="steel_crm", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    gen = sub.add_parser("generate-data", help="write a synthetic Dynamics 365 Sales export")
    gen.add_argument("--out", type=Path, default=Path("data/dynamics_export"))
    gen.add_argument("--seed", type=int, default=42)
    gen.add_argument("--start", default="2024-07-01")
    gen.add_argument("--end", default="2026-06-30")
    r = sub.add_parser("run", help="check, model and analyse an export, then write the dashboard data")
    r.add_argument("--export", type=Path, default=Path("data/dynamics_export"))
    r.add_argument("--out", type=Path, default=Path("data/processed"))
    r.add_argument("--dashboard", type=Path, default=Path("dashboard/data.js"))
    b = sub.add_parser("bpmn", help="write the BPMN 2.0 process diagrams (.bpmn)")
    b.add_argument("--out", type=Path, default=Path("docs/bpmn"))
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
                        datefmt="%H:%M:%S")
    if args.command == "generate-data":
        tables = generate_export(args.out, args.seed, args.start, args.end)
        rows = sum(len(df) for df in tables.values())
        print(f"Wrote {len(tables)} tables ({rows:,} rows) to {args.out}")
    elif args.command == "bpmn":
        for path in write_bpmn(all_processes(), args.out):
            print(f"Wrote {path}")
    else:
        summary = run(args.export, args.out, args.dashboard)
        k, m = summary["kpi"], summary["model"]
        print(f"\nLast 12 months: {k['revenue'] / 1e12:,.1f}T IRR from {k['tons']:,.0f} t, "
              f"{k['margin_per_ton'] / 1e6:,.1f}M IRR margin per ton, win rate {k['win_rate']:.0%}.")
        print(f"Win model AUC {m['auc_model']:.2f} vs the CRM's own probability {m['auc_crm']:.2f} "
              f"({m['n_test']} deals closed after {m['split_date']}). Done in {summary['seconds']}s.")


if __name__ == "__main__":
    main()
