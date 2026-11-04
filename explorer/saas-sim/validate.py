"""Does the report find what the simulator planted? Also: what survives when the log is missing fields?

    python validate.py [out/calls.jsonl] [out/truth.json]
"""
import json, sys, tempfile
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from unitecon.analyze import analyze
from unitecon.load import load

PRICES = Path(__file__).resolve().parents[1] / "unitecon" / "prices.json"
PLANS = {"free": Decimal(0), "pro": Decimal(20)}


def run(path):
    return analyze(load(str(path), str(PRICES)), PLANS)


def strip(src, drop, dst):
    with open(src) as f, open(dst, "w") as g:
        for line in f:
            e = json.loads(line)
            if e["type"] == "call":
                for k in drop:
                    e.pop(k, None)
            g.write(json.dumps(e) + "\n")


def check(r, truth, label, ids_comparable=True):
    found = set(r["stuck"]["ids"])
    planted = set(truth["loop_sessions"])
    tp = len(found & planted)
    long = sum(len(v["ids"]) for v in r["long_sessions"].values())
    loops = (f"loops {tp}/{len(planted)} found, {len(found - planted)} false" if ids_comparable else
             f"loops {len(found)} found (truth {len(planted)}; ids rebuilt)") + (f", {long} long sessions" if long else "")
    top = {t["user"] for t in r["top_users"]}
    planted_users = set(truth["whales"]) | set(truth["heavy_pro"])
    shifts = [s for s in r["prompt_shifts"] if s["to"] == truth["bloat_hash"]]
    print(f"{label:34} {loops} | planted heavy users in top 8: "
          f"{len(top & planted_users)}/{len(planted_users)} | retries {r['failed_calls']['count']}/{truth['retry_calls']} | "
          f"prompt shift {'found ' + format(shifts[0]['change'], '+.0%') if shifts else 'MISSED'}")


if __name__ == "__main__":
    log = sys.argv[1] if len(sys.argv) > 1 else "out/calls.jsonl"
    truth = json.load(open(sys.argv[2] if len(sys.argv) > 2 else "out/truth.json"))
    check(run(log), truth, "full log")
    with tempfile.TemporaryDirectory() as d:
        for label, drop in [("no session_id (rebuilt from gaps)", ["session_id"]), ("no tool_sig", ["tool_sig"]),
                            ("no prefix_hash / prefix_tokens", ["prefix_hash", "prefix_tokens"]),
                            ("no feature", ["feature"]), ("no user_id", ["user_id"]),
                            ("only model + tokens + ts", ["session_id", "user_id", "feature", "tool_sig", "prefix_hash", "prefix_tokens", "attempt"])]:
            dst = Path(d) / "x.jsonl"
            strip(log, drop, dst)
            try:
                check(run(dst), truth, label, ids_comparable="session_id" not in drop)
            except Exception as exc:
                print(f"{label:34} CRASH {type(exc).__name__}: {exc}")
