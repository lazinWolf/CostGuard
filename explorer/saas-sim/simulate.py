"""Simulate a month of traffic for a small AI SaaS and write it as a call log (JSONL).

SYNTHETIC. Every parameter below is my assumption, not data. The point is to give the report
something realistic in shape (heavy-tailed users, growing context, agent loops, retries, a prompt
regression) and a ground-truth file (truth.json) so we can check whether the report finds what was
planted. Results here say nothing about real-world cost levels.

    python simulate.py [--users 800] [--seed 7] [--out out]
"""
import argparse, json, math, random
from pathlib import Path

BASE = 1_790_812_800          # 2026-10-01T00:00:00Z
DAY = 86_400
BLOAT_DAY = 15                # chat system prompt grows from v1 to v2 on this day


def clamp(x, lo, hi):
    return max(lo, min(hi, x))


class Sim:
    def __init__(self, seed):
        self.rng = random.Random(seed)
        self.events, self.n_call = [], 0
        self.truth = {"whales": [], "heavy_pro": [], "loop_sessions": [], "bloat_day": BLOAT_DAY,
                      "bloat_hash": "chat:v2", "retry_calls": 0}

    def call(self, t, sid, uid, feature, model, inp, out, prefix, prefix_tokens, tool_sig=None, attempt=1, error=False):
        self.n_call += 1
        self.events.append({"type": "call", "ts": round(t, 2), "call_id": f"c{self.n_call}", "session_id": sid,
                            "user_id": uid, "feature": feature, "model": model, "input_tokens": inp,
                            "output_tokens": 0 if error else out, "cached_input_tokens": 0,
                            "latency_ms": 30000 if error else int(clamp(out * 9 + self.rng.gauss(400, 120), 150, 29000)),
                            "status": "error" if error else "ok", "attempt": attempt,
                            "prefix_hash": prefix, "prefix_tokens": prefix_tokens,
                            **({"tool_sig": tool_sig} if tool_sig else {})})

    def maybe_retry(self, t, *args, **kw):
        """2.5% of calls time out after the input was billed, then succeed on a second attempt."""
        if self.rng.random() < 0.025:
            self.truth["retry_calls"] += 1
            self.call(t, *args, **kw, attempt=1, error=True)
            self.call(t + 30, *args, **kw, attempt=2)
            return 30
        self.call(t, *args, **kw)
        return 0

    def chat(self, t0, sid, uid, turns):
        rng, history = self.rng, 0
        late = (t0 - BASE) / DAY >= BLOAT_DAY
        prefix, ptok = ("chat:v2", 2600) if late else ("chat:v1", 1400)
        t = t0
        for _ in range(turns):
            user = int(rng.uniform(30, 140))
            out = int(clamp(rng.lognormvariate(math.log(180), 0.5), 20, 900))
            t += self.maybe_retry(t, sid, uid, "chat", "gpt-4o-mini", ptok + history + user, out, prefix, ptok)
            history += user + out
            t += rng.uniform(15, 90)
        return rng.random() < 0.88

    def doc(self, t0, sid, uid):
        rng = self.rng
        inp = 600 + int(clamp(rng.lognormvariate(math.log(12000), 0.8), 800, 90000))
        out = int(clamp(rng.lognormvariate(math.log(450), 0.3), 100, 1500))
        self.maybe_retry(t0, sid, uid, "doc_summary", "gpt-4o", inp, out, "doc:v3", 600)
        return rng.random() < 0.92

    def research(self, t0, sid, uid, loop=False):
        rng, history, t = self.rng, 0, t0
        steps = rng.randint(40, 70) if loop else rng.randint(5, 14)
        if loop:
            self.truth["loop_sessions"].append(sid)
        for step in range(steps):
            out = int(clamp(rng.lognormvariate(math.log(160), 0.4), 30, 700))
            last = step == steps - 1 and not loop
            sig = None if last else ("search:err-timeout" if loop and step >= 4 else f"search:{rng.randrange(16**6):06x}")
            t += self.maybe_retry(t, sid, uid, "research_agent", "gpt-4o", 2200 + history, out, "research:v1", 2200, tool_sig=sig)
            history += out + (900 if loop and step >= 4 else int(rng.uniform(2000, 5000)))
            t += rng.uniform(3, 8)
        return (not loop) and rng.random() < 0.72


def simulate(users=800, seed=7):
    s = Sim(seed)
    rng = s.rng
    n_session = 0

    def session(uid, feature, t0, **kw):
        nonlocal n_session
        n_session += 1
        sid = f"s{n_session}"
        if feature == "chat":
            ok = s.chat(t0, sid, uid, kw.get("turns") or min(25, int(rng.expovariate(1 / 4)) + 1))
        elif feature == "doc_summary":
            ok = s.doc(t0, sid, uid)
        else:
            ok = s.research(t0, sid, uid, loop=kw.get("loop", False))
        s.events.append({"type": "outcome", "session_id": sid, "success": ok})

    plans = {}
    for i in range(users):
        uid = f"u{i:04d}"
        plans[uid] = "pro" if rng.random() < 0.12 else "free"
        s.events.append({"type": "user", "user_id": uid, "plan": plans[uid]})
    free = [u for u, p in plans.items() if p == "free"]
    pro = [u for u, p in plans.items() if p == "pro"]
    whales = rng.sample(free, 3)
    heavy_pro = rng.sample(pro, 2)
    s.truth["whales"], s.truth["heavy_pro"] = whales, heavy_pro

    for uid, plan in plans.items():
        if rng.random() > (0.95 if plan == "pro" else 0.7):
            continue                                            # registered, never used
        n = max(1, round(rng.lognormvariate(math.log(22 if plan == "pro" else 5), 0.9)))
        mix = (("chat", .45), ("doc_summary", .30), ("research_agent", .25)) if plan == "pro" else \
              (("chat", .70), ("doc_summary", .25), ("research_agent", .05))
        for _ in range(n):
            feature = rng.choices([m[0] for m in mix], [m[1] for m in mix])[0]
            loop = feature == "research_agent" and rng.random() < 0.04
            session(uid, feature, BASE + rng.uniform(0, 30 * DAY), loop=loop)
    for uid in whales:                                          # scripted free-tier abuse: very long chats
        for _ in range(rng.randint(250, 400)):
            session(uid, "chat", BASE + rng.uniform(0, 30 * DAY), turns=rng.randint(40, 80))
    for uid in heavy_pro:                                       # a pro user who lives in the agent
        for _ in range(rng.randint(70, 100)):
            session(uid, "research_agent", BASE + rng.uniform(0, 30 * DAY))
    s.events.sort(key=lambda e: e.get("ts", 0))
    return s


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--users", type=int, default=800)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--out", default="out")
    a = ap.parse_args()
    sim = simulate(a.users, a.seed)
    out = Path(a.out)
    out.mkdir(exist_ok=True)
    (out / "calls.jsonl").write_text("".join(json.dumps(e) + "\n" for e in sim.events))
    (out / "truth.json").write_text(json.dumps(sim.truth, indent=1))
    print(f"{sum(e['type'] == 'call' for e in sim.events)} calls, {a.users} users -> {out}/calls.jsonl")
