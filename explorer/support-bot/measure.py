"""What I (a builder) had to write to get CostGuard evidence out of my own app."""
import sys, time, json
from decimal import Decimal
sys.path.insert(0, "../..")
from costguard.hook import ArtifactRecorder
from costguard.models import PricingCatalog, ModelPrice
import app, cases

PRICING = PricingCatalog(version="openai-2026-11", source="typed by hand from the pricing page", entries=[
    ModelPrice(provider="openai", model="gpt-4o", input_per_million_usd=Decimal("2.50"), output_per_million_usd=Decimal("10")),
    ModelPrice(provider="openai", model="gpt-4o-mini", input_per_million_usd=Decimal("0.15"), output_per_million_usd=Decimal("0.60"))])

def measure(name, **kw):
    rec = ArtifactRecorder(suite_id="support-bot", name=name, pricing=PRICING, configuration={"model": kw.get("model", "gpt-4o")})
    for cid, ticket, must in cases.CASES:
        with rec.case(cid) as c:
            t = time.monotonic()
            def on_call(r, _t=[None]):
                now = time.monotonic(); c.record_chat(provider="openai", model=r["model"], response=r,
                                                      latency_ms=Decimal(str(round((now - on_call.t) * 1000, 2)))); on_call.t = now
            on_call.t = t
            out = app.respond(ticket, on_call=on_call, **kw)
            c.score(Decimal(1 if must.lower() in out.lower() else 0), "contains")
    return rec.artifact()

if __name__ == "__main__":
    variants = {"baseline": {}, "warm-prompt": {"system_prompt": "You are Acme's warm, empathetic support bot. Always acknowledge feelings. Use the policy below.\n\n{policy}"},
                "mini": {"model": "gpt-4o-mini"}}
    for name, kw in variants.items():
        open(f"{name}.json", "w").write(measure(name, **kw).model_dump_json(indent=1))
        print("wrote", name)
