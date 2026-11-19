from __future__ import annotations

import json
import queue
import threading
import unittest

from savva.agent.codex_app_server import CodexAppServerAdapter, _merge_rate_limit_update, _rpc_error
from savva.agent.models import ModelAdapterError


_END = object()


class FakeStdout:
    def __init__(self):
        self.lines = queue.Queue()

    def __iter__(self):
        return self

    def __next__(self):
        line = self.lines.get(timeout=2)
        if line is _END:
            raise StopIteration
        return line


class FakeStdin:
    def __init__(self, stdout: FakeStdout, *, close_without_response: bool = False,
                 tool_call: bool = False):
        self.stdout = stdout
        self.close_without_response = close_without_response
        self.tool_call = tool_call

    def write(self, raw: str):
        request = json.loads(raw)
        if self.close_without_response:
            self.stdout.lines.put(_END)
            return len(raw)
        self.stdout.lines.put(json.dumps({"id": request["id"], "result": {"turnId": "turn-1"}}))
        if self.tool_call:
            self.stdout.lines.put(json.dumps({"id": "tool-req", "method": "item/tool/call",
                                              "params": {"callId": "call-1", "tool": "savva_files_propose",
                                                         "arguments": {"path": "a.txt"}}}))
        else:
            self.stdout.lines.put(json.dumps({"method": "item/agentMessage/delta",
                                              "params": {"delta": "live "}}))
            self.stdout.lines.put(json.dumps({"method": "item/agentMessage/delta",
                                              "params": {"delta": "response"}}))
            self.stdout.lines.put(json.dumps({"method": "turn/completed",
                                              "params": {"turn": {"id": "turn-1", "status": "completed",
                                                                    "usage": {"inputTokens": 3, "outputTokens": 2}}}}))
        self.stdout.lines.put(_END)
        return len(raw)

    def flush(self):
        pass


class FakeProcess:
    def __init__(self, *, close_without_response: bool = False, tool_call: bool = False):
        self.stdout = FakeStdout()
        self.stdin = FakeStdin(self.stdout, close_without_response=close_without_response,
                               tool_call=tool_call)

    def poll(self):
        return None


class CodexDispatchTests(unittest.TestCase):
    def test_partial_quota_notification_preserves_full_read_snapshot(self):
        original = {"ordinaryUsageAllowed": True,
                    "rateLimits": {"limitId": "codex", "primary": {"usedPercent": 20, "resetsAt": 500},
                                   "secondary": {"usedPercent": 40, "resetsAt": 900},
                                   "rateLimitReachedType": None},
                    "rateLimitsByLimitId": {"codex": {"limitId": "codex", "primary": {"usedPercent": 20},
                                                         "secondary": {"usedPercent": 40}}}}
        merged = _merge_rate_limit_update(original, {"limitId": "codex", "primary": {"usedPercent": 30},
                                                     "secondary": None, "rateLimitReachedType": None})
        self.assertTrue(merged["ordinaryUsageAllowed"])
        self.assertEqual(merged["rateLimits"]["primary"]["usedPercent"], 30)
        self.assertEqual(merged["rateLimits"]["primary"]["resetsAt"], 500)
        self.assertEqual(merged["rateLimits"]["secondary"]["usedPercent"], 40)
        self.assertEqual(merged["rateLimits"]["secondary"]["resetsAt"], 900)
        self.assertEqual(merged["rateLimitsByLimitId"]["codex"]["secondary"]["usedPercent"], 40)

    def test_turn_completed_after_turn_start_rpc_is_delivered(self):
        adapter = CodexAppServerAdapter(workspace_root=".")
        adapter._process = FakeProcess()
        reader = threading.Thread(target=adapter._read_stdout, daemon=True)
        reader.start()

        response = adapter._rpc("turn/start", {"threadId": "thread-1"}, timeout=1)
        self.assertEqual(response["result"]["turnId"], "turn-1")
        turn = adapter._wait_for_turn(timeout=1)
        self.assertEqual(turn.content, "live response")
        self.assertEqual(turn.usage.total_tokens, 5)
        reader.join(timeout=1)

    def test_closed_server_unblocks_pending_rpc(self):
        adapter = CodexAppServerAdapter(workspace_root=".")
        adapter._process = FakeProcess(close_without_response=True)
        reader = threading.Thread(target=adapter._read_stdout, daemon=True)
        reader.start()
        with self.assertRaises(ModelAdapterError) as raised:
            adapter._rpc("turn/start", {}, timeout=1)
        self.assertEqual(raised.exception.code, "CODEX_DISCONNECTED")
        reader.join(timeout=1)

    def test_server_tool_call_is_routed_as_event(self):
        adapter = CodexAppServerAdapter(workspace_root=".")
        adapter._process = FakeProcess(tool_call=True)
        adapter._tool_names["savva_files_propose"] = "files.propose"
        reader = threading.Thread(target=adapter._read_stdout, daemon=True)
        reader.start()
        adapter._rpc("turn/start", {}, timeout=1)
        result = adapter._wait_for_turn(timeout=1)
        self.assertEqual(result.tool_calls[0].name, "files.propose")
        self.assertEqual(result.tool_calls[0].arguments, {"path": "a.txt"})
        reader.join(timeout=1)

    def test_only_specific_codex_errors_classify_as_quota(self):
        snapshot = {"rateLimits": {"primary": {"usedPercent": 10}}}
        self.assertEqual(_rpc_error({"data": {"codexErrorInfo": "rateLimitExceeded"}}, snapshot).failure_kind, "quota")
        self.assertEqual(_rpc_error({"data": {"codexErrorInfo": "serverOverloaded"}}, snapshot).failure_kind, "overload")
        self.assertEqual(_rpc_error({"data": {"codexErrorInfo": "requestTimeout"}}, snapshot).failure_kind, "transient")


if __name__ == "__main__":
    unittest.main()
