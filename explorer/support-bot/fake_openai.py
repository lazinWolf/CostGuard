"""Stand-in for the OpenAI API: realistic enough shapes (usage, tool calls, verbosity by model/prompt)."""
import json, math, re
from http.server import BaseHTTPRequestHandler, HTTPServer

def toks(s): return max(1, math.ceil(len(s) / 4))

def reply(body):
    msgs, model = body["messages"], body["model"]
    system = msgs[0]["content"]; user = next(m["content"] for m in msgs if m["role"] == "user")
    tool_results = [m for m in msgs if m["role"] == "tool"]
    m = re.search(r"order #(\d+)", user)
    if body.get("tools") and m and not tool_results:
        return {"tool_calls": [{"id": "c1", "type": "function", "function": {"name": "lookup_order", "arguments": json.dumps({"order_id": m.group(1)})}}]}, "tool_calls"
    small = "mini" in model
    wants_refund = "refund" in user.lower() or "money back" in user.lower()
    in_window = "days ago" in user and int(re.search(r"(\d+) days ago", user).group(1)) <= 30
    if wants_refund:
        # the small model sometimes misreads the 30 day window
        ok = in_window if not small or "tricky" not in user else (not in_window)
        text = "You're eligible for a full refund within 30 days of purchase; I've started it." if ok else "Sorry, this is outside our 30-day refund window, so I can't refund it."
    elif "password" in user.lower():
        text = "Use the Forgot password link on the sign-in page and we will email you a reset link."
    else:
        text = "Thanks for reaching out! A teammate will follow up within one business day."
    if "warm" in system.lower() or "empath" in system.lower():
        text = "I completely understand how frustrating this must be, and I'm really sorry for the trouble. " + text + " Please let me know if there is anything else I can help you with today, we are always happy to help!"
    if small and "tricky" not in user: text = text
    return {"content": text}, "stop"

class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        msg, why = reply(body)
        prompt = "".join(str(m.get("content") or m.get("tool_calls") or "") for m in body["messages"])
        out = msg.get("content") or json.dumps(msg.get("tool_calls"))
        data = {"id": "x", "object": "chat.completion", "model": body["model"],
                "choices": [{"index": 0, "message": {"role": "assistant", **msg}, "finish_reason": why}],
                "usage": {"prompt_tokens": toks(prompt) + 40, "completion_tokens": toks(out)}}
        raw = json.dumps(data).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(raw))); self.end_headers(); self.wfile.write(raw)

if __name__ == "__main__":
    HTTPServer(("127.0.0.1", 9911), H).serve_forever()
