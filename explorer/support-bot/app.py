"""A typical small support-bot: system prompt + policy doc in context + one order-lookup tool."""
import json, httpx
POLICY = open("policy.md").read()
PROMPT_V1 = "You are Acme's support bot. Be concise. Use the policy below.\n\n{policy}"
ORDERS = {"4821": {"status": "delivered", "days_ago": 12}, "9100": {"status": "delivered", "days_ago": 45}}
TOOLS = [{"type": "function", "function": {"name": "lookup_order", "description": "Look up an order",
          "parameters": {"type": "object", "properties": {"order_id": {"type": "string"}}}}}]

def respond(ticket, system_prompt=PROMPT_V1, model="gpt-4o", on_call=None):
    messages = [{"role": "system", "content": system_prompt.format(policy=POLICY)}, {"role": "user", "content": ticket}]
    for _ in range(4):
        r = httpx.post("http://127.0.0.1:9911/v1/chat/completions", json={"model": model, "messages": messages, "tools": TOOLS}).json()
        if on_call: on_call(r)
        m = r["choices"][0]["message"]
        if not m.get("tool_calls"): return m["content"]
        messages.append(m)
        for tc in m["tool_calls"]:
            o = ORDERS[json.loads(tc["function"]["arguments"])["order_id"]]
            messages.append({"role": "tool", "tool_call_id": tc["id"], "content": json.dumps(o)})
    return ""
