from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

from savva.agent.config import (
    DEFAULT_OPENROUTER_MODEL,
    AppSettings,
    SettingsStore,
)
from savva.agent.context import ContextBudgetManager, IntentRouter
from savva.agent.models import (
    ModelAdapter,
    ModelResponse,
    ModelUsage,
    OpenRouterAdapter,
    ToolCall,
)
from savva.agent.network import TailscaleExitStatus, TailscaleInspector
from savva.agent.orchestrator import AgentOrchestrator, _with_verified_summary
from savva.agent.projects import ProjectService
from savva.agent.sanitizer import constrain_read_only_answer, sanitize_for_model
from savva.agent.tools import build_agent_registry
from savva.agent.usage import UsageLogger
from savva.agent.workspace import SafeWorkspace
from savva.core.registry import ToolExecutionError, ToolRegistry
from savva.core.schemas import ToolContract


class FakeTransport:
    def __init__(self, response: dict[str, Any]):
        self.response = response
        self.calls: list[dict[str, Any]] = []

    def post_json(self, url, payload, headers, timeout_seconds):
        self.calls.append(
            {
                "url": url,
                "payload": payload,
                "headers": headers,
                "timeout": timeout_seconds,
            }
        )
        return self.response


class FakeAdapter(ModelAdapter):
    provider = "openrouter"
    model = "test/model"

    def __init__(self, responses: list[ModelResponse]):
        self.responses = responses
        self.calls: list[dict[str, Any]] = []

    def complete(
        self,
        messages,
        tools,
        *,
        max_tokens,
        timeout_seconds,
        tool_choice="auto",
    ):
        self.calls.append(
            {
                "messages": messages,
                "tools": tools,
                "tool_choice": tool_choice,
            }
        )
        return self.responses.pop(0)


def response(
    content: str = "",
    calls: list[ToolCall] | None = None,
    cost: float = 0.0,
) -> ModelResponse:
    return ModelResponse(
        content=content,
        tool_calls=calls or [],
        usage=ModelUsage(10, 5, 15, cost),
        finish_reason="stop",
        request_id="request-test",
    )


def contract(name: str) -> ToolContract:
    return ToolContract(
        name=name,
        description="Test read-only tool",
        parameters={
            "type": "object",
            "properties": {"value": {"type": "string"}},
            "additionalProperties": False,
        },
        permissions=("system.read",),
        risk_level="read_only",
        timeout_seconds=1,
    )


class ModelAdapterTests(unittest.TestCase):
    def test_openrouter_payload_and_tool_call_parsing(self) -> None:
        transport = FakeTransport(
            {
                "id": "gen-1",
                "choices": [
                    {
                        "message": {
                            "content": "",
                            "tool_calls": [
                                {
                                    "id": "call-1",
                                    "type": "function",
                                    "function": {
                                        "name": "software.detect",
                                        "arguments": '{"name":"node"}',
                                    },
                                }
                            ],
                        },
                        "finish_reason": "tool_calls",
                    }
                ],
                "usage": {
                    "prompt_tokens": 21,
                    "completion_tokens": 8,
                    "total_tokens": 29,
                    "cost": 0,
                },
            }
        )
        adapter = OpenRouterAdapter(
            model=DEFAULT_OPENROUTER_MODEL,
            base_url="https://openrouter.ai/api/v1",
            api_key="test-api-key-not-real",
            transport=transport,
        )

        result = adapter.complete(
            [{"role": "user", "content": "Check Node"}],
            [{"type": "function", "function": {"name": "software.detect"}}],
            max_tokens=256,
            timeout_seconds=20,
        )

        self.assertEqual(result.tool_calls[0].name, "software.detect")
        self.assertEqual(result.tool_calls[0].arguments, {"name": "node"})
        self.assertEqual(result.usage.total_tokens, 29)
        self.assertTrue(result.usage.cost_verified)
        self.assertEqual(transport.calls[0]["url"], "https://openrouter.ai/api/v1/chat/completions")
        self.assertEqual(
            transport.calls[0]["headers"]["Authorization"],
            "Bearer test-api-key-not-real",
        )
        self.assertNotIn(
            "test-api-key-not-real",
            json.dumps(transport.calls[0]["payload"]),
        )

    def test_default_model_is_free_and_tool_capable(self) -> None:
        self.assertEqual(DEFAULT_OPENROUTER_MODEL, "nvidia/nemotron-3.5-lightning:free")


class SanitizerTests(unittest.TestCase):
    def test_secrets_emails_and_personal_paths_are_removed(self) -> None:
        data = {
            "api_key": "test-api-key-not-real",
            "note": "Bearer abc.def.ghi user@example.com C:\\Users\\Alice\\project",
        }

        clean = sanitize_for_model(data)
        serialized = json.dumps(clean)

        self.assertNotIn("test-api-key-not-real", serialized)
        self.assertNotIn("user@example.com", serialized)
        self.assertNotIn("Alice", serialized)
        self.assertIn("<redacted>", serialized)

    def test_read_only_answer_removes_command_instructions(self) -> None:
        answer = (
            "Environment is ready.\n"
            "Run npm install to prepare dependencies.\n"
            "Use npx later.\n"
            "Open a terminal for the next step.\n"
            "Create a new React project with Vite.\n"
            "Start the development server afterwards.\n"
            "The browser should open http://localhost:5173.\n"
            "РЎРѕР·РґР°Р№С‚Рµ РїР°РїРєСѓ РїСЂРѕРµРєС‚Р° Рё РїСЂРёСЃС‚СѓРїР°Р№С‚Рµ Рє СЃРѕР·РґР°РЅРёСЋ РєРѕРјРїРѕРЅРµРЅС‚Р°.\n"
            "РџРѕР·Р¶Рµ РјРѕР¶РЅРѕ РїРѕРґРєР»СЋС‡РёС‚СЊ TypeScript.\n"
            "```bash\nnpx create-react-app demo\n```\n"
            "Choose a template as the next planning step."
        )

        clean = constrain_read_only_answer(answer)

        self.assertNotIn("npm install", clean)
        self.assertNotIn("create-react-app", clean)
        self.assertNotIn("READ_ONLY_FOOTER", clean)
        self.assertTrue(clean.startswith("Environment is ready."))

    def test_verified_summary_uses_real_tool_data(self) -> None:
        events = [
            {
                "type": "tool_call",
                "result": {
                    "status": "success",
                    "data": {
                        "tools": [
                            {
                                "name": "node",
                                "installed": True,
                                "version": "22.1",
                            }
                        ]
                    },
                },
            }
        ]

        answer = _with_verified_summary("Model answer", events)

        self.assertIn("- node: 22.1", answer)
        self.assertIn("Model answer", answer)

    def test_tailscale_status_parser_selects_current_exit_node(self) -> None:
        status = TailscaleInspector.parse_status(
            {
                "BackendState": "Running",
                "ExitNodeStatus": {"ID": "node-1", "Online": True},
                "Peer": {
                    "internal-key-that-does-not-match-id": {
                        "ID": "node-1",
                        "HostName": "squealing-orange",
                        "DNSName": "squealing-orange.example.ts.net.",
                        "TailscaleIPs": ["100.87.196.23"],
                        "Online": True,
                    }
                },
            }
        )

        self.assertTrue(status.ready)
        self.assertEqual(status.exit_node_hostname, "squealing-orange")
        self.assertEqual(
            status.exit_node_dns_name, "squealing-orange.example.ts.net"
        )


class OrchestratorTests(unittest.TestCase):
    def test_chat_uses_no_tools_short_prompt_and_no_footer(self) -> None:
        adapter = FakeAdapter([response(content="Dinner suggestion.")])
        with tempfile.TemporaryDirectory() as directory:
            orchestrator = AgentOrchestrator(
                adapter=adapter, registry=build_agent_registry(directory),
                workspace_root=directory, max_tokens=2048, max_tool_steps=3,
                max_requests_per_session=5, max_request_cost_usd=0.1,
                max_session_cost_usd=0.5, max_duration_seconds=30,
                usage_logger=UsageLogger(Path(directory) / "usage.jsonl"),
            )
            result = orchestrator.run([], "??? ??????????? ?? ?????", session_id="chat-test")
        self.assertEqual(adapter.calls[0]["tools"], [])
        self.assertEqual(adapter.calls[0]["messages"][0]["role"], "system")
        self.assertEqual(result.answer, "Dinner suggestion.")

    def test_history_budget_limits_model_copy_without_mutating_history(self) -> None:
        manager = ContextBudgetManager()
        history = [
            {"role": "user", "content": f"old question {index} " + ("x" * 5000)}
            for index in range(4)
        ]
        original_size = len(history)
        plan = manager.build(history, "What do you know about yourself?", [], "openrouter", "test/model", 2048)
        self.assertEqual(plan.intent, "CHAT")
        self.assertEqual(len(history), original_size)
        self.assertEqual(plan.tools, [])
        self.assertLess(plan.estimates["history"], 1400)
        self.assertLessEqual(plan.max_output_tokens, 160)

    def test_development_and_other_intents_are_explicit(self) -> None:
        russian_fix = "".join(map(chr, [0x0418,0x0441,0x043F,0x0440,0x0430,0x0432,0x044C,0x0020,0x043E,0x0448,0x0438,0x0431,0x043A,0x0443,0x0020,0x0432,0x0020,0x0050,0x0079,0x0074,0x0068,0x006F,0x006E,0x002D,0x043C,0x043E,0x0434,0x0443,0x043B,0x0435]))
        russian_presentation = "".join(map(chr, [0x0421,0x043E,0x0437,0x0434,0x0430,0x0439,0x0020,0x043F,0x0440,0x0435,0x0437,0x0435,0x043D,0x0442,0x0430,0x0446,0x0438,0x044E]))
        self.assertEqual(IntentRouter.route(russian_fix), "DEVELOPMENT")
        self.assertEqual(IntentRouter.route("What do you know about me?"), "CHAT")
        self.assertEqual(IntentRouter.route(russian_presentation), "UNSUPPORTED_OTHER")

    def test_development_tools_are_scoped_and_keep_approval_gate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            orchestrator = AgentOrchestrator(
                adapter=FakeAdapter([response(content="Ready")]),
                registry=build_agent_registry(directory), workspace_root=directory,
                max_tokens=512, max_tool_steps=1, max_requests_per_session=2,
                max_request_cost_usd=0.1, max_session_cost_usd=0.5,
                max_duration_seconds=10, usage_logger=UsageLogger(Path(directory) / "u.jsonl"),
            )
            plan = orchestrator.context_manager.build(
                [{"role": "user", "content": "Create a Python module"}],
                "Create a Python module", orchestrator._model_tools(), "openrouter", "test/model", 512,
            )
        names = [tool["function"]["name"] for tool in plan.tools]
        self.assertEqual(plan.intent, "DEVELOPMENT")
        self.assertTrue(names)
        self.assertFalse(any("files_apply" in name for name in names))

    def test_project_create_is_executed_as_a_proposal_without_writes(self) -> None:
        adapter = FakeAdapter(
            [
                response(
                    calls=[
                        ToolCall(
                            "call-project",
                            "savva_project_create",
                            {
                                "project_name": "demo-store",
                                "title": "SAVVA Computers",
                            },
                            '{"project_name":"demo-store","title":"SAVVA Computers"}',
                        )
                    ]
                ),
                response(content="Proposal is ready for review."),
            ]
        )
        with tempfile.TemporaryDirectory() as directory:
            service = ProjectService(directory)
            orchestrator = AgentOrchestrator(
                adapter=adapter,
                registry=build_agent_registry(directory, service),
                workspace_root=directory,
                max_tokens=512,
                max_tool_steps=3,
                max_requests_per_session=5,
                max_request_cost_usd=0.1,
                max_session_cost_usd=0.5,
                max_duration_seconds=30,
                usage_logger=UsageLogger(Path(directory) / "usage.jsonl"),
                project_service=service,
            )

            result = orchestrator.run(
                [], "Create the SAVVA store", session_id="project-session-agent"
            )

            proposal_events = [
                event for event in result.events if event["type"] == "proposal_ready"
            ]
            self.assertEqual(len(proposal_events), 1)
            self.assertEqual(proposal_events[0]["proposal"]["status"], "pending")
            self.assertFalse((Path(directory) / "demo-store").exists())

    def test_multiple_tool_calls_are_executed_and_results_are_returned(self) -> None:
        registry = ToolRegistry()
        registry.register(
            contract("demo.echo"),
            lambda args: {
                "value": args["value"],
                "api_key": "must-not-reach-provider",
                "path": "C:\\Users\\Alice\\project",
            },
        )
        registry.register(
            contract("demo.second"),
            lambda args: {"value": args["value"]},
        )
        adapter = FakeAdapter(
            [
                response(
                    calls=[
                        ToolCall("call-1", "demo.echo", {"value": "node"}, '{"value":"node"}'),
                        ToolCall("call-2", "demo.second", {"value": "git"}, '{"value":"git"}'),
                    ]
                ),
                response(content="Environment checked."),
            ]
        )
        with tempfile.TemporaryDirectory() as directory:
            usage_path = Path(directory) / "usage.jsonl"
            orchestrator = AgentOrchestrator(
                adapter=adapter,
                registry=registry,
                workspace_root=directory,
                max_tokens=512,
                max_tool_steps=3,
                max_requests_per_session=5,
                max_request_cost_usd=0.1,
                max_session_cost_usd=0.5,
                max_duration_seconds=30,
                usage_logger=UsageLogger(usage_path),
            )

            result = orchestrator.run([], "Check my tools", session_id="session-123")
            records = usage_path.read_text(encoding="utf-8").splitlines()

        tool_events = [event for event in result.events if event["type"] == "tool_call"]
        exposed_names = [tool["function"]["name"] for tool in adapter.calls[0]["tools"]]
        self.assertEqual(len(tool_events), 2)
        self.assertEqual(tool_events[0]["result"]["status"], "success")
        self.assertTrue(result.answer.startswith("Environment checked."))
        self.assertEqual(result.usage["requests"], 2)
        self.assertEqual(len(records), 2)
        self.assertIn('"intent": "ACTION"', records[0])
        self.assertIn('"prompt_version": "1.0.0"', records[0])
        self.assertIn('"cost_verified": false', records[0])
        self.assertTrue(all("." not in name for name in exposed_names))
        self.assertIn("savva_demo_echo", exposed_names)
        outbound = json.dumps(adapter.calls[1]["messages"])
        self.assertNotIn("must-not-reach-provider", outbound)
        self.assertNotIn("Alice", outbound)

    def test_unknown_tool_is_returned_as_safe_error(self) -> None:
        adapter = FakeAdapter(
            [
                response(
                    calls=[
                        ToolCall("call-x", "shell.execute", {"cmd": "whoami"}, '{"cmd":"whoami"}')
                    ]
                ),
                response(content="I cannot use that tool."),
            ]
        )
        with tempfile.TemporaryDirectory() as directory:
            orchestrator = AgentOrchestrator(
                adapter=adapter,
                registry=ToolRegistry(),
                workspace_root=directory,
                max_tokens=256,
                max_tool_steps=2,
                max_requests_per_session=3,
                max_request_cost_usd=0,
                max_session_cost_usd=0,
                max_duration_seconds=30,
                usage_logger=UsageLogger(Path(directory) / "usage.jsonl"),
            )
            result = orchestrator.run([], "Run shell", session_id="session-456")

        tool_event = next(event for event in result.events if event["type"] == "tool_call")
        self.assertEqual(tool_event["result"]["error"]["code"], "TOOL_NOT_ALLOWED_FOR_INTENT")

    def test_chat_cannot_execute_registered_tool_even_if_model_returns_call(self) -> None:
        invoked = []
        registry = ToolRegistry()
        registry.register(contract("demo.echo"), lambda args: invoked.append(args))
        adapter = FakeAdapter([
            response(calls=[ToolCall("call-1", "savva_demo_echo", {"value": "x"}, '{"value":"x"}')]),
            response(content="A dinner idea.")
        ])
        with tempfile.TemporaryDirectory() as directory:
            orchestrator = AgentOrchestrator(
                adapter=adapter, registry=registry, workspace_root=directory,
                max_tokens=512, max_tool_steps=2, max_requests_per_session=3,
                max_request_cost_usd=0.1, max_session_cost_usd=0.5,
                max_duration_seconds=10, usage_logger=UsageLogger(Path(directory) / "usage.jsonl"),
            )
            result = orchestrator.run([], "What should I cook?", session_id="chat-deny-tool")
        self.assertEqual(invoked, [])
        self.assertEqual(adapter.calls[0]["tools"], [])
        self.assertEqual(result.answer, "A dinner idea.")


class WorkspaceAndSettingsTests(unittest.TestCase):
    def test_workspace_read_is_confined(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "package.json").write_text('{"name":"demo"}', encoding="utf-8")
            workspace = SafeWorkspace(root)

            result = workspace.read_text("package.json")
            listing = workspace.list_directory(".")
            with self.assertRaises(ToolExecutionError):
                workspace.read_text("..\\outside.txt")

        self.assertEqual(result["content"], '{"name":"demo"}')
        self.assertEqual(listing["entries"][0]["path"], "package.json")

    def test_settings_store_accepts_numeric_strings_and_never_contains_key(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings = AppSettings(workspace_root=str(root / "workspace"))
            store = SettingsStore(settings, root / "settings.json")

            updated = store.update(
                {
                    "max_tokens": "1024",
                    "max_tool_steps": "4",
                    "max_request_cost_usd": "0.01",
                }
            )
            public = updated.to_public_dict()

        self.assertEqual(updated.max_tokens, 1024)
        self.assertEqual(updated.max_tool_steps, 4)
        self.assertNotIn("api_key", public)
        self.assertNotIn("OPENROUTER_API_KEY", public)


if __name__ == "__main__":
    unittest.main()
