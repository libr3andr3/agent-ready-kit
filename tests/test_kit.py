"""End-to-end tests: the real server on a free port, with a fake OpenAI-compatible model next to it.

Run: python3 -m unittest discover -s tests -v
"""
import http.client
import http.server
import json
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path

KIT = Path(__file__).resolve().parent.parent
MODEL_CALLS = []


class FakeModel(http.server.BaseHTTPRequestHandler):
    """First call asks for a website check of a private address (the SSRF guard must refuse it), then answers."""

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        MODEL_CALLS.append(body)
        last = body["messages"][-1]
        if last["role"] == "user" and "check" in last["content"].lower() and body.get("tools"):
            msg = {"role": "assistant", "content": "", "tool_calls": [{"id": "t1", "type": "function", "function": {
                "name": "check_agent_readiness", "arguments": json.dumps({"url": "http://169.254.169.254/latest"})}}]}
        else:
            msg = {"role": "assistant", "content": "We open Tuesday to Sunday, 7 am to 3 pm."}
        data = json.dumps({"choices": [{"message": msg}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass


def serve(handler):
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


TMP = tempfile.mkdtemp(prefix="agent-kit-test-")
FAKE = serve(FakeModel)
os.environ.update(SITE_CONFIG=str(KIT / "site.example.json"), SITE_DB=str(Path(TMP) / "site.db"),
                  LLM_BASE_URL=f"http://127.0.0.1:{FAKE.server_address[1]}/v1", LLM_API_KEY="", LLM_MODEL="fake")
sys.path.insert(0, str(KIT / "server"))
import app  # noqa: E402

app.identity.configure(app.CFG["url"], app.CFG["name"])
app.identity.init(str(Path(TMP) / "identity.key"), app.db)
SRV = serve(app.Handler)
PORT = SRV.server_address[1]


def req(method, path, body=None, headers=None):
    c = http.client.HTTPConnection("127.0.0.1", PORT, timeout=30)
    data = json.dumps(body).encode() if body is not None and not isinstance(body, bytes) else body
    h = {"Content-Type": "application/json", **(headers or {})}
    c.request(method, path, body=data, headers=h)
    r = c.getresponse()
    raw = r.read()
    try:
        return r.status, json.loads(raw) if raw else None, r
    except json.JSONDecodeError:
        return r.status, raw, r


def rpc(path, method, params=None, rid=1, headers=None):
    return req("POST", path, {"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}}, headers)


class Discovery(unittest.TestCase):
    def test_health(self):
        s, j, _ = req("GET", "/api/health")
        self.assertEqual(s, 200)
        self.assertTrue(j["ok"] and j["configured"])

    def test_agent_card(self):
        s, j, r = req("GET", "/.well-known/agent-card.json")
        self.assertEqual(s, 200)
        self.assertEqual(j["url"], "https://bakery.example/a2a")
        self.assertEqual(j["protocolVersion"], "0.3.0")
        self.assertEqual(r.getheader("Access-Control-Allow-Origin"), "*")

    def test_mcp_card(self):
        s, j, _ = req("GET", "/.well-known/mcp.json")
        self.assertEqual(s, 200)
        self.assertEqual(j["remotes"][0]["url"], "https://bakery.example/mcp")
        self.assertIn("get_business_info", [t["name"] for t in j["tools"]])

    def test_widget_config(self):
        s, j, _ = req("GET", "/api/widget-config")
        self.assertEqual(s, 200)
        self.assertEqual(j["languages"], ["en", "es"])
        self.assertEqual(j["texts"]["es"]["chips"][0][0], "Horario")
        self.assertEqual(j["texts"]["en"]["assistant"], "Mia")

    def test_widget_files_and_traversal(self):
        s, raw, r = req("GET", "/agent-kit/chat.js")
        self.assertEqual(s, 200)
        self.assertIn("javascript", r.getheader("Content-Type"))
        s, _, _ = req("GET", "/agent-kit/../server/app.py")
        self.assertEqual(s, 404)
        s, _, _ = req("GET", "/agent-kit/%2e%2e/server/app.py")
        self.assertEqual(s, 404)


class MCP(unittest.TestCase):
    def test_initialize_negotiates(self):
        s, j, _ = rpc("/mcp", "initialize", {"protocolVersion": "2025-06-18"})
        self.assertEqual(j["result"]["protocolVersion"], "2025-06-18")
        s, j, _ = rpc("/mcp", "initialize", {"protocolVersion": "1999-01-01"})
        self.assertEqual(j["result"]["protocolVersion"], "2025-11-25")
        self.assertEqual(j["result"]["serverInfo"]["title"], "Example Bakery")

    def test_notification_and_batch_and_version(self):
        s, j, _ = req("POST", "/mcp", {"jsonrpc": "2.0", "method": "notifications/initialized"})
        self.assertEqual((s, j), (202, None))
        s, j, _ = req("POST", "/mcp", [{"jsonrpc": "2.0", "id": 1, "method": "ping"}])
        self.assertEqual(s, 400)
        s, j, _ = rpc("/mcp", "ping", headers={"MCP-Protocol-Version": "1999-01-01"})
        self.assertEqual(s, 400)

    def test_tools(self):
        s, j, _ = rpc("/mcp", "tools/list")
        names = [t["name"] for t in j["result"]["tools"]]
        for n in ("get_business_info", "list_services", "search", "fetch", "check_agent_readiness"):
            self.assertIn(n, names)
        s, j, _ = rpc("/mcp", "tools/call", {"name": "list_services", "arguments": {"lang": "es"}})
        svc = j["result"]["structuredContent"]["services"]
        self.assertEqual(svc[0]["name"], "Pan de masa madre")
        self.assertEqual(svc[0]["price"], "$9")
        s, j, _ = rpc("/mcp", "tools/call", {"name": "search", "arguments": {"query": "custom cake"}})
        first = j["result"]["structuredContent"]["results"][0]["id"]
        self.assertEqual(first, "service:custom_cake")
        s, j, _ = rpc("/mcp", "tools/call", {"name": "fetch", "arguments": {"id": "doc:allergens"}})
        self.assertIn("nuts", j["result"]["structuredContent"]["text"])
        s, j, _ = rpc("/mcp", "tools/call", {"name": "nope"})
        self.assertEqual(j["error"]["code"], -32602)

    def test_ssrf_guard(self):
        for url in ("http://127.0.0.1", "http://169.254.169.254/latest/meta-data", "http://localhost:8146",
                    "http://[::1]/", "http://10.0.0.1", "https://bakery.example:8443"):
            s, j, _ = rpc("/mcp", "tools/call", {"name": "check_agent_readiness", "arguments": {"url": url}})
            self.assertTrue(j["result"]["isError"], url)

    def test_payload_limit(self):
        s, j, _ = req("POST", "/mcp", b"{" + b" " * 40_000 + b"}")
        self.assertEqual(s, 413)

    def test_identity_challenge(self):
        if not app.identity_on():
            self.skipTest("no ML-DSA openssl and no cryptography here")
        s, j, _ = req("POST", "/identity/challenge", b"{}")
        self.assertEqual(s, 200)
        self.assertTrue(j["message"].startswith("bakery.example identity v1\n"))


class ModelPaths(unittest.TestCase):
    def test_chat_answers(self):
        s, j, _ = req("POST", "/api/chat", {"messages": [{"role": "user", "content": "When are you open?"}], "lang": "en"})
        self.assertEqual(s, 200)
        self.assertIn("Tuesday", j["reply"])
        system = MODEL_CALLS[-1]["messages"][0]["content"]
        self.assertIn("Example Bakery", system)
        self.assertIn("$9", system)

    def test_chat_tool_loop_blocks_private_address(self):
        s, j, _ = req("POST", "/api/chat", {"messages": [{"role": "user", "content": "Check my site please"}], "lang": "en"})
        self.assertEqual(s, 200)
        self.assertEqual(j["cards"], [])  # the SSRF guard refused, so there is no readiness card
        tool_msgs = [m for m in MODEL_CALLS[-1]["messages"] if m["role"] == "tool"]
        self.assertIn("couldn't reach", tool_msgs[-1]["content"])

    def test_chat_rejects_bad_input(self):
        s, _, _ = req("POST", "/api/chat", {"messages": [{"role": "assistant", "content": "hi"}]})
        self.assertEqual(s, 400)

    def test_save_lead_needs_consent(self):
        self.assertFalse(app.save_lead({"email": "a@b.co", "use_case": "x"}, "en")["saved"])
        self.assertTrue(app.save_lead({"email": "a@b.co", "use_case": "x", "consent": True}, "en")["saved"])

    def test_a2a(self):
        s, j, _ = rpc("/a2a", "message/send", {"message": {"role": "user", "messageId": "m1",
                                                           "parts": [{"kind": "text", "text": "Opening hours?"}]}})
        self.assertEqual(s, 200)
        self.assertEqual(j["result"]["kind"], "message")
        self.assertIn("Tuesday", j["result"]["parts"][0]["text"])
        s, j, _ = rpc("/a2a", "tasks/get", {"id": "x"})
        self.assertEqual(j["error"]["code"], -32001)
        s, j, _ = rpc("/a2a", "message/stream", {})
        self.assertEqual(j["error"]["code"], -32004)


if __name__ == "__main__":
    unittest.main()
