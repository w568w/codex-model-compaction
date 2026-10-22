#!/usr/bin/env python3
"""Exercise the packaged app-server against a local Responses gateway."""

import argparse
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import queue
import subprocess
import tempfile
import threading
import time
import unittest
import uuid

MODELS = ("gpt-compaction-test", "kimi-compaction-test")
CODEX = None


class Gateway(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_GET(self):
        self.send_error(404)

    def do_POST(self):
        raw = self.rfile.read(int(self.headers["Content-Length"]))
        if self.headers.get("Content-Encoding") == "zstd":
            raw = subprocess.check_output(["zstd", "--decompress", "--stdout"], input=raw)
        request = json.loads(raw)
        model = request["model"]
        remote = any(item.get("type") == "compaction_trigger" for item in request.get("input", []) if isinstance(item, dict))
        local = not remote and "COMPACTION_TEST_SUMMARIZE" in json.dumps(request)
        kind = "remote" if remote else "local" if local else "normal"
        with self.server.lock:
            self.server.requests.append((model, kind, request))
            tool = not (remote or local) and model not in self.server.tools_emitted
            if tool:
                self.server.tools_emitted.add(model)
            tokens = 100 if remote or local or tool else self.server.tokens.get(model, 100)
        if remote:
            output = {"type": "compaction", "id": "cmp_" + uuid.uuid4().hex,
                      "encrypted_content": "MOCK_REMOTE_CONTEXT"}
        elif tool:
            output = {"type": "function_call", "id": "fc_" + uuid.uuid4().hex,
                      "call_id": "call_" + uuid.uuid4().hex, "name": "exec_command",
                      "arguments": json.dumps({"cmd": "printf compaction-tool-ok", "max_output_tokens": 100})}
        else:
            output = {"type": "message", "id": "msg_" + uuid.uuid4().hex, "role": "assistant",
                      "status": "completed", "phase": "final_answer",
                      "content": [{"type": "output_text", "text": "MOCK_SUMMARY_CONTEXT" if local else "MOCK_REPLY", "annotations": []}]}
        response_id = "resp_" + uuid.uuid4().hex
        events = [
            {"type": "response.created", "response": {"id": response_id}},
            {"type": "response.output_item.added", "output_index": 0, "item": output},
            {"type": "response.output_item.done", "output_index": 0, "item": output},
            {"type": "response.completed", "response": {"id": response_id, "model": model,
             "status": "completed", "output": [output], "usage": {"input_tokens": tokens,
             "output_tokens": 10, "total_tokens": tokens + 10}}},
        ]
        body = "".join(f"event: {event['type']}\ndata: {json.dumps(event)}\n\n" for event in events).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class AppServer:
    def __init__(self, codex, home, log):
        self.log = log.open("w")
        self.process = subprocess.Popen([str(codex), "app-server", "--listen", "stdio://", "--strict-config"],
                                        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.log,
                                        text=True, bufsize=1, env={**os.environ, "CODEX_HOME": str(home)})
        self.messages = queue.Queue()
        self.pending = []
        self.serial = 0
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()
        try:
            self.request("initialize", {"clientInfo": {"name": "compaction-tests", "version": "1"},
                                       "capabilities": {"experimentalApi": True}})
        except Exception:
            self.close()
            print(log.read_text())
            raise
        self.send({"method": "initialized"})

    def _read(self):
        for line in self.process.stdout:
            self.messages.put(json.loads(line))
        self.messages.put({"process_exited": self.process.poll()})

    def send(self, message):
        self.process.stdin.write(json.dumps(message) + "\n")
        self.process.stdin.flush()

    def wait(self, predicate, timeout=60):
        deadline = time.monotonic() + timeout
        for index, message in enumerate(self.pending):
            if predicate(message):
                return self.pending.pop(index)
        while True:
            try:
                message = self.messages.get(timeout=max(0.01, deadline - time.monotonic()))
            except queue.Empty:
                raise TimeoutError(f"app-server timeout; pending={self.pending[-8:]}") from None
            if "process_exited" in message:
                raise RuntimeError(f"app-server exited: {message}")
            if "id" in message and "method" in message:
                raise RuntimeError(f"unexpected server request: {message}")
            if predicate(message):
                return message
            self.pending.append(message)

    def request(self, method, params):
        self.serial += 1
        request_id = self.serial
        self.send({"id": request_id, "method": method, "params": params})
        response = self.wait(lambda value: value.get("id") == request_id)
        if "error" in response:
            raise RuntimeError(f"{method}: {response['error']}")
        return response.get("result")

    def completed(self, thread_id):
        event = self.wait(lambda value: value.get("method") == "turn/completed"
                          and value.get("params", {}).get("threadId") == thread_id)
        turn = event["params"]["turn"]
        if turn.get("status") != "completed":
            raise RuntimeError(f"failed turn: {turn}")

    def close(self):
        self.process.stdin.close()
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.terminate()
            self.process.wait(timeout=5)
        self.reader.join(timeout=2)
        self.process.stdout.close()
        self.log.close()


class CompactionTests(unittest.TestCase):
    def setUp(self):
        test_cache = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "codex-compaction-tests"
        test_cache.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix="run-", dir=test_cache)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.gateway = ThreadingHTTPServer(("127.0.0.1", 0), Gateway)
        self.addCleanup(self.gateway.server_close)
        self.addCleanup(self.gateway.shutdown)
        self.gateway.requests, self.gateway.tools_emitted, self.gateway.tokens = [], set(), {}
        self.gateway.lock = threading.Lock()
        threading.Thread(target=self.gateway.serve_forever, daemon=True).start()
        home = self.root / "home"
        home.mkdir()
        (self.root / "instructions.txt").write_text("You are a test assistant. Use exec_command when requested.\n")
        catalog = []
        for slug in MODELS:
            catalog.append({"slug": slug, "display_name": slug, "description": "Local test model",
                            "base_instructions": "You are a local test assistant.",
                            "supported_reasoning_levels": [], "shell_type": "unified_exec", "visibility": "list",
                            "supported_in_api": True, "priority": 0, "support_verbosity": False,
                            "truncation_policy": {"mode": "tokens", "limit": 10000},
                            "experimental_supported_tools": [], "context_window": 100000,
                            "include_apps_usage_instructions": False, "tool_mode": "direct"})
        (home / "models.json").write_text(json.dumps({"models": catalog}))
        (home / "config.toml").write_text(f'''model = "{MODELS[0]}"
model_provider = "shared"
model_catalog_json = {json.dumps(str(home / 'models.json'))}
model_instructions_file = {json.dumps(str(self.root / 'instructions.txt'))}
approval_policy = "never"
sandbox_mode = "danger-full-access"
compact_prompt = "COMPACTION_TEST_SUMMARIZE"
[model_providers.shared]
name = "OpenAI"
base_url = "http://127.0.0.1:{self.gateway.server_port}/v1"
wire_api = "responses"
[features]
enable_request_compression = false
remote_models = false
memories = false
apps = false
plugins = false
''')
        self.log = self.root / "app-server.log"
        self.app = AppServer(CODEX, home, self.log)
        self.addCleanup(self.app.close)

    def tearDown(self):
        if self._outcome and not self._outcome.success and self.log.exists():
            print(self.log.read_text()[-16000:])

    def thread(self, model, *, automatic=False, background=False):
        result = self.app.request("thread/start", {"model": model, "cwd": str(self.root),
                                  "approvalPolicy": "never", "sandbox": "danger-full-access",
                                  "config": {"features.async_compaction": {"enabled": background,
                                             "model_async_compact_threshold_percent": 50},
                                             "model_auto_compact_token_limit": 30000 if automatic else 90000}})
        return result["thread"]["id"]

    def turn(self, thread, model=None):
        params = {"threadId": thread, "input": [{"type": "text", "text": "Run a tool, then reply.", "text_elements": []}]}
        if model:
            params["model"] = model
        self.app.request("turn/start", params)

    def kinds(self):
        with self.gateway.lock:
            return Counter((model, kind) for model, kind, _ in self.gateway.requests)

    def test_manual_concurrent_and_model_switch(self):
        threads = [self.thread(model) for model in MODELS]
        for thread in threads:
            self.turn(thread)
        for thread in threads:
            self.app.completed(thread)
        for thread in threads:
            self.app.request("thread/compact/start", {"threadId": thread})
        for thread in threads:
            self.app.completed(thread)
        counts = self.kinds()
        self.assertEqual(counts[(MODELS[0], "remote")], 1)
        self.assertEqual(counts[(MODELS[1], "local")], 1)
        self.assertEqual(counts[(MODELS[1], "remote")], 0)
        for thread in threads:
            self.turn(thread)
        for thread in threads:
            self.app.completed(thread)
        for model, marker in zip(MODELS, ("MOCK_REMOTE_CONTEXT", "MOCK_SUMMARY_CONTEXT")):
            latest = [request for name, kind, request in self.gateway.requests if name == model and kind == "normal"][-1]
            self.assertIn(marker, json.dumps(latest["input"]))
            self.assertTrue(any(name == model and kind == "normal" and
                                "function_call_output" in json.dumps(request["input"]) and
                                "compaction-tool-ok" in json.dumps(request["input"])
                                for name, kind, request in self.gateway.requests), "tool execution must complete")
        self.turn(threads[1], MODELS[0])
        self.app.completed(threads[1])
        self.app.request("thread/compact/start", {"threadId": threads[1]})
        self.app.completed(threads[1])
        self.assertEqual(self.kinds()[(MODELS[0], "remote")], 2)
        self.turn(threads[1], MODELS[1])
        self.app.completed(threads[1])
        self.app.request("thread/compact/start", {"threadId": threads[1]})
        self.app.completed(threads[1])
        self.assertEqual(self.kinds()[(MODELS[1], "local")], 2)
        self.assertEqual(self.kinds()[(MODELS[1], "remote")], 0)

    def test_foreground_automatic_compaction(self):
        threads = [self.thread(model, automatic=True) for model in MODELS]
        self.gateway.tokens.update({model: 40000 for model in MODELS})
        for thread in threads:
            self.turn(thread)
        for thread in threads:
            self.app.completed(thread)
        self.gateway.tokens.clear()
        for thread in threads:
            self.turn(thread)
        for thread in threads:
            self.app.completed(thread)
        counts = self.kinds()
        self.assertGreaterEqual(counts[(MODELS[0], "remote")], 1)
        self.assertGreaterEqual(counts[(MODELS[1], "local")], 1)
        self.assertEqual(counts[(MODELS[1], "remote")], 0)

    def test_background_remote_compaction_is_gpt_only(self):
        threads = [self.thread(model, background=True) for model in MODELS]
        self.gateway.tokens.update({model: 60000 for model in MODELS})
        for thread in threads:
            self.turn(thread)
        for thread in threads:
            self.app.completed(thread)
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline and not self.kinds()[(MODELS[0], "remote")]:
            time.sleep(0.05)
        self.assertGreaterEqual(self.kinds()[(MODELS[0], "remote")], 1)
        # Wait for any incorrectly scheduled Kimi task, rather than asserting immediately.
        time.sleep(0.5)
        self.assertEqual(self.kinds()[(MODELS[1], "remote")], 0)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--codex", required=True, type=Path)
    parser.add_argument("--expected-version")
    args, unittest_args = parser.parse_known_args()
    CODEX = args.codex.resolve()
    if args.expected_version:
        actual = subprocess.check_output([str(CODEX), "--version"], text=True).strip()
        if actual != f"codex-cli {args.expected_version}":
            parser.error(f"backend version mismatch: {actual}")
    unittest.main(argv=[__file__, *unittest_args], verbosity=2)
