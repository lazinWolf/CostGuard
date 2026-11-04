"""Do the importers reproduce the native report? Compare numbers, not headlines.

    python export_as.py out_small/calls.jsonl out_small --max-calls=6000    (first)
    python roundtrip.py [out_small]
"""
import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from unitecon import adapters
from unitecon.analyze import analyze
from unitecon.load import load
import csv, json, tempfile

here = Path(__file__).resolve().parent
folder = Path(sys.argv[1]) if len(sys.argv) > 1 else here / "out_small"
PRICES = str(here.parent / "unitecon" / "prices.json")
PLANS = {"free": Decimal(0), "pro": Decimal(20)}


def run(path):
    if adapters.is_native(str(path)):
        log = str(path)
    else:
        events, fmt, _ = adapters.convert(str(path))
        handle = tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False)
        handle.write("".join(json.dumps(e) + "\n" for e in events))
        handle.close()
        log = handle.name
    data = load(log, PRICES)
    for row in csv.DictReader(open(folder / "users.csv")):
        data.plans[row["user_id"]] = row["plan"]
    return analyze(data, PLANS)


native = run(folder / "subset.jsonl")
rows = [("total cost", lambda r: r["total_cost"]), ("sessions", lambda r: r["sessions"]),
        ("cost chat", lambda r: r["by_feature"]["chat"]["cost"]), ("cost research", lambda r: r["by_feature"]["research_agent"]["cost"]),
        ("failed calls", lambda r: r["failed_calls"]["count"]), ("stuck sessions", lambda r: r["stuck"]["count"]),
        ("cache saving", lambda r: round(r["caching"]["saving"], 8)), ("cache eligible calls", lambda r: r["caching"]["eligible_calls"]),
        ("top user", lambda r: r["top_users"][0]["user"]), ("pro margin", lambda r: round(r["plans"]["pro"]["margin"], 8)),
        ("long sessions", lambda r: sum(len(v["ids"]) for v in r["long_sessions"].values()))]
results = {"native": native}
for name in ("langfuse.json", "helicone.csv", "otel.jsonl"):
    results[name] = run(folder / name)
print(f"{'':22}" + "".join(f"{n:>16}" for n in results))
for label, get in rows:
    cells = []
    for n, r in results.items():
        try:
            v = get(r)
            cells.append(f"{v:.6f}" if isinstance(v, Decimal) else str(v))
        except Exception as exc:
            cells.append(type(exc).__name__)
    same = ["" if c == cells[0] else "*" for c in cells]
    print(f"{label:22}" + "".join(f"{c + s:>16}" for c, s in zip(cells, same)))
print("\n* differs from native")
