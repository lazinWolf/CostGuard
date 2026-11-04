"""python -m unitecon report calls.jsonl --plans plans.json [--prices prices.json] [--format md|json]"""
import argparse
import json
import sys
from decimal import Decimal
from pathlib import Path

from .analyze import analyze
from .load import load
from .render import render


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="unitecon")
    sub = p.add_subparsers(dest="command", required=True)
    r = sub.add_parser("report", help="unit economics from a call log")
    r.add_argument("log")
    r.add_argument("--plans", required=True, help='JSON: {"plans": {"free": {"price_usd_month": "0"}}}')
    r.add_argument("--prices", default=str(Path(__file__).with_name("prices.json")))
    r.add_argument("--format", choices=["md", "json"], default="md")
    a = p.parse_args(argv)
    plan_prices = {k: Decimal(v["price_usd_month"]) for k, v in json.loads(Path(a.plans).read_text())["plans"].items()}
    result = analyze(load(a.log, a.prices), plan_prices)
    print(json.dumps(result, default=str, indent=1) if a.format == "json" else render(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
