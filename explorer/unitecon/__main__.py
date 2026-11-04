"""python -m unitecon report calls.jsonl --plans plans.json [--prices prices.json] [--format md|json]"""
import argparse
import json
import sys
from decimal import Decimal
from pathlib import Path

import tempfile

from . import adapters
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
    c = sub.add_parser("convert", help="turn a Langfuse, Helicone or OpenTelemetry export into a unitecon call log")
    c.add_argument("export")
    c.add_argument("--format", choices=["auto", *adapters.FORMATS], default="auto")
    c.add_argument("-o", "--output", help="file to write (default: standard output)")
    r.add_argument("--users", help="CSV with columns user_id,plan: which plan each registered user is on (exports do not carry it)")
    r.add_argument("--format-in", choices=["auto", *adapters.FORMATS], default="auto",
                   help="format of the log; auto reads unitecon's own and the supported exports")
    a = p.parse_args(argv)
    if a.command == "convert":
        events, fmt, notes = adapters.convert(a.export, a.format)
        text = "".join(json.dumps(e) + "\n" for e in events)
        Path(a.output).write_text(text) if a.output else sys.stdout.write(text)
        print(f"read {len(events):,} calls as {fmt}" + "".join(f"; {n}" for n in notes), file=sys.stderr)
        return 0
    plan_prices = {k: Decimal(v["price_usd_month"]) for k, v in json.loads(Path(a.plans).read_text())["plans"].items()}
    log, notes = a.log, []
    if a.format_in != "auto" or not adapters.is_native(a.log):
        events, fmt, notes = adapters.convert(a.log, a.format_in)
        handle = tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False)
        handle.write("".join(json.dumps(e) + "\n" for e in events))
        handle.close()
        log, notes = handle.name, [f"read as a {fmt} export"] + notes
    data = load(log, a.prices)
    if a.users:
        import csv
        for row in csv.DictReader(open(a.users)):
            data.plans[row["user_id"]] = row["plan"]
        data.warnings = [w for w in data.warnings if "no plan record" not in w]
        if any(u not in {row["user_id"] for row in csv.DictReader(open(a.users))} for u in {c.user for c in data.calls}):
            data.warnings.append("some users in the log are not in the --users file; they have no plan or revenue")
    result = analyze(data, plan_prices)
    result["notes"] = notes
    print(json.dumps(result, default=str, indent=1) if a.format == "json" else render(result))
    return 0


def run(argv=None) -> int:
    try:
        return main(argv)
    except (adapters.FormatError, FileNotFoundError, KeyError) as exc:
        print(f"unitecon: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(run())
