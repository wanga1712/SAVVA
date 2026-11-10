"""Local-only HTTP backend for the SAVVA chat interface."""

from __future__ import annotations

import json
import os
import threading
from collections import OrderedDict
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from .agent.config import (
    AppSettings,
    SettingsError,
    SettingsStore,
    default_usage_log_path,
    settings_from_environment,
)
from .agent.models import ModelAdapterError, OllamaAdapter, OpenRouterAdapter
from .agent.network import TailscaleExitStatus, TailscaleInspector
from .agent.orchestrator import AgentOrchestrator, AgentRuntimeError
from .agent.projects import ActionJournal, ProjectService
from .agent.tools import build_agent_registry
from .agent.usage import UsageLogger

MAX_REQUEST_BODY = 65_536
MAX_MESSAGE_LENGTH = 8_000


@dataclass(slots=True)
class SessionState:
    messages: list[dict[str, Any]]
    requests: int = 0
    cost_usd: float = 0.0


class SessionStore:
    def __init__(self, max_sessions: int = 100):
        self._sessions: OrderedDict[str, SessionState] = OrderedDict()
        self._max_sessions = max_sessions
        self._lock = threading.Lock()

    def get(self, session_id: str) -> SessionState:
        with self._lock:
            state = self._sessions.get(session_id)
            if session_id in self._sessions:
                self._sessions.move_to_end(session_id)
            if state is None:
                return SessionState([])
            return SessionState(list(state.messages), state.requests, state.cost_usd)

    def set(self, session_id: str, state: SessionState) -> None:
        with self._lock:
            self._sessions[session_id] = SessionState(
                list(state.messages), state.requests, state.cost_usd
            )
            self._sessions.move_to_end(session_id)
            while len(self._sessions) > self._max_sessions:
                self._sessions.popitem(last=False)

    def clear(self, session_id: str) -> None:
        with self._lock:
            self._sessions.pop(session_id, None)


class AppRuntime:
    def __init__(
        self,
        project_root: Path | None = None,
        settings_path: Path | None = None,
        usage_path: Path | None = None,
        action_log_path: Path | None = None,
        tailscale_inspector: TailscaleInspector | None = None,
    ):
        self.project_root = project_root or Path(__file__).resolve().parent.parent
        try:
            settings = settings_from_environment(self.project_root)
        except SettingsError:
            settings = AppSettings(
                workspace_root=str(self.project_root / "workspace")
            )
            settings.validate()
        self.settings_store = SettingsStore(settings, settings_path)
        self.settings_store.load()
        self.usage_logger = UsageLogger(usage_path or default_usage_log_path())
        self.action_journal = ActionJournal(
            action_log_path or default_usage_log_path().with_name("actions.jsonl")
        )
        self.sessions = SessionStore()
        self.tailscale_inspector = tailscale_inspector or TailscaleInspector()
        self._project_services: dict[str, ProjectService] = {}
        self._project_services_lock = threading.Lock()

    def settings(self) -> AppSettings:
        return self.settings_store.get()

    def api_key(self) -> str | None:
        if self.settings().provider != "openrouter":
            return None
        value = os.environ.get("OPENROUTER_API_KEY", "").strip()
        return value or None

    def public_settings(self) -> dict[str, Any]:
        result = self.settings().to_public_dict()
        result["api_key_configured"] = self.api_key() is not None
        result["api_key_source"] = "OPENROUTER_API_KEY"
        return result

    def tools(self) -> list[dict[str, Any]]:
        root = self.settings().workspace_root
        return build_agent_registry(root, self.project_service(root)).list_tools()

    def project_service(self, workspace_root: str | None = None) -> ProjectService:
        root = str(Path(workspace_root or self.settings().workspace_root).resolve())
        with self._project_services_lock:
            service = self._project_services.get(root)
            if service is None:
                service = ProjectService(root, self.action_journal)
                self._project_services[root] = service
            return service

    def tailscale_status(self) -> TailscaleExitStatus:
        return self.tailscale_inspector.inspect()

    def chat(self, message: str, session_id: str) -> dict[str, Any]:
        settings = self.settings()
        egress = self.tailscale_status()
        self._validate_egress(settings, egress)
        key = self.api_key()
        if settings.provider == "openrouter" and key is None:
            raise AgentRuntimeError(
                "API_KEY_MISSING",
                "Set OPENROUTER_API_KEY in the backend environment before starting SAVVA.",
            )
        adapter = self._adapter(settings, key)
        project_service = self.project_service(settings.workspace_root)
        orchestrator = AgentOrchestrator(
            adapter=adapter,
            registry=build_agent_registry(settings.workspace_root, project_service),
            workspace_root=settings.workspace_root,
            max_tokens=settings.max_tokens,
            max_tool_steps=settings.max_tool_steps,
            max_requests_per_session=settings.max_requests_per_session,
            max_request_cost_usd=settings.max_request_cost_usd,
            max_session_cost_usd=settings.max_session_cost_usd,
            max_duration_seconds=settings.max_duration_seconds,
            usage_logger=self.usage_logger,
            project_service=project_service,
        )
        previous = self.sessions.get(session_id)
        result = orchestrator.run(
            previous.messages,
            message,
            session_id=session_id,
            initial_requests=previous.requests,
            initial_cost_usd=previous.cost_usd,
        )
        self.sessions.set(
            session_id,
            SessionState(
                messages=result.messages,
                requests=result.usage["requests"],
                cost_usd=result.usage["cost_usd"],
            ),
        )
        return {
            "status": "success",
            "session_id": session_id,
            "answer": result.answer,
            "events": result.events,
            "usage": result.usage,
            "provider": settings.provider,
            "model": settings.model,
            "egress": egress.to_dict(),
        }

    @staticmethod
    def _validate_egress(
        settings: AppSettings, egress: TailscaleExitStatus
    ) -> None:
        if not settings.require_tailscale_exit_node:
            return
        if not egress.ready:
            raise AgentRuntimeError(
                "TAILSCALE_EXIT_NODE_UNAVAILABLE",
                "SAVVA requires an online Tailscale exit node, but none is selected.",
            )
        expected = settings.expected_exit_node.strip().lower().rstrip(".")
        if expected:
            candidates = {
                (egress.exit_node_hostname or "").lower(),
                (egress.exit_node_dns_name or "").lower().rstrip("."),
            }
            if expected not in candidates:
                raise AgentRuntimeError(
                    "TAILSCALE_EXIT_NODE_MISMATCH",
                    f"Expected Tailscale exit node '{expected}', but another node is selected.",
                )

    @staticmethod
    def _adapter(settings: AppSettings, key: str | None):
        if settings.provider == "openrouter":
            return OpenRouterAdapter(
                model=settings.model,
                base_url=settings.base_url,
                api_key=key or "",
                reasoning_effort=settings.reasoning_effort,
            )
        return OllamaAdapter(model=settings.model, base_url=settings.base_url)


class SavvaHTTPServer(ThreadingHTTPServer):
    runtime: AppRuntime


class SavvaRequestHandler(BaseHTTPRequestHandler):
    server: SavvaHTTPServer
    web_root = Path(__file__).resolve().parent / "web"

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        try:
            if path == "/api/health":
                settings = self.server.runtime.public_settings()
                egress = self.server.runtime.tailscale_status()
                self._json(
                    HTTPStatus.OK,
                    {
                        "status": "ok",
                        "provider": settings["provider"],
                        "model": settings["model"],
                        "api_key_configured": settings["api_key_configured"],
                        "workspace_root": settings["workspace_root"],
                        "require_tailscale_exit_node": settings[
                            "require_tailscale_exit_node"
                        ],
                        "expected_exit_node": settings["expected_exit_node"],
                        "tailscale": egress.to_dict(),
                    },
                )
                return
            if path == "/api/settings":
                self._json(HTTPStatus.OK, self.server.runtime.public_settings())
                return
            if path == "/api/tools":
                self._json(
                    HTTPStatus.OK,
                    {"status": "success", "tools": self.server.runtime.tools()},
                )
                return
            if path == "/api/network":
                self._json(
                    HTTPStatus.OK,
                    {
                        "status": "success",
                        "tailscale": self.server.runtime.tailscale_status().to_dict(),
                    },
                )
                return
            if path == "/api/projects/tree":
                query = parse_qs(urlparse(self.path).query)
                requested = query.get("path", ["."])[0]
                tree = self.server.runtime.project_service().inspect_tree(requested, 4)
                self._json(HTTPStatus.OK, {"status": "success", **tree})
                return
            if path == "/api/files/content":
                query = parse_qs(urlparse(self.path).query)
                requested = query.get("path", [""])[0]
                content = self.server.runtime.project_service().read_file(requested)
                self._json(HTTPStatus.OK, {"status": "success", **content})
                return
            if path.startswith("/api/proposals/"):
                proposal_id = path.removeprefix("/api/proposals/")
                query = parse_qs(urlparse(self.path).query)
                session_id = query.get("session_id", [""])[0]
                if not 8 <= len(session_id) <= 100:
                    self._error(
                        HTTPStatus.BAD_REQUEST,
                        "INVALID_SESSION",
                        "A valid session_id is required.",
                    )
                    return
                proposal = self.server.runtime.project_service().get_confirmation(
                    proposal_id, session_id
                )
                self._json(HTTPStatus.OK, {"status": "success", "proposal": proposal})
                return
            if path == "/api/actions":
                self._json(
                    HTTPStatus.OK,
                    {
                        "status": "success",
                        "actions": self.server.runtime.action_journal.recent(100),
                    },
                )
                return
            if path in {"/", "/index.html"}:
                self._static("index.html", "text/html; charset=utf-8")
                return
            if path == "/app.js":
                self._static("app.js", "text/javascript; charset=utf-8")
                return
            if path == "/styles.css":
                self._static("styles.css", "text/css; charset=utf-8")
                return
            self._error(HTTPStatus.NOT_FOUND, "NOT_FOUND", "Resource not found.")
        except Exception:
            self._error(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                "INTERNAL_ERROR",
                "The request could not be completed.",
            )

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        try:
            body = self._read_json()
            if path == "/api/settings":
                settings = self.server.runtime.settings_store.update(body)
                public = settings.to_public_dict()
                public["api_key_configured"] = self.server.runtime.api_key() is not None
                self._json(HTTPStatus.OK, {"status": "success", "settings": public})
                return
            if path == "/api/chat":
                message = body.get("message")
                session_id = body.get("session_id")
                if not isinstance(message, str) or not message.strip():
                    self._error(
                        HTTPStatus.BAD_REQUEST,
                        "INVALID_MESSAGE",
                        "Message must be a non-empty string.",
                    )
                    return
                if len(message) > MAX_MESSAGE_LENGTH:
                    self._error(
                        HTTPStatus.BAD_REQUEST,
                        "MESSAGE_TOO_LARGE",
                        "Message exceeds the 8000-character limit.",
                    )
                    return
                if not isinstance(session_id, str) or not 8 <= len(session_id) <= 100:
                    self._error(
                        HTTPStatus.BAD_REQUEST,
                        "INVALID_SESSION",
                        "A valid session_id is required.",
                    )
                    return
                result = self.server.runtime.chat(message.strip(), session_id)
                self._json(HTTPStatus.OK, result)
                return
            if path == "/api/reset":
                session_id = body.get("session_id")
                if isinstance(session_id, str):
                    self.server.runtime.sessions.clear(session_id)
                self._json(HTTPStatus.OK, {"status": "success"})
                return
            if path == "/api/confirm":
                proposal_id = body.get("proposal_id")
                token = body.get("confirmation_token")
                session_id = body.get("session_id")
                decision = body.get("decision")
                if not all(
                    isinstance(value, str) and value
                    for value in (proposal_id, token, session_id, decision)
                ):
                    self._error(
                        HTTPStatus.BAD_REQUEST,
                        "CONFIRMATION_INVALID",
                        "proposal_id, confirmation_token, session_id, and decision are required.",
                    )
                    return
                service = self.server.runtime.project_service()
                if decision == "approve":
                    proposal = service.apply(
                        proposal_id=proposal_id,
                        confirmation_token=token,
                        session_id=session_id,
                    )
                elif decision == "reject":
                    proposal = service.reject(
                        proposal_id=proposal_id,
                        confirmation_token=token,
                        session_id=session_id,
                    )
                else:
                    self._error(
                        HTTPStatus.BAD_REQUEST,
                        "DECISION_INVALID",
                        "decision must be approve or reject.",
                    )
                    return
                self._json(
                    HTTPStatus.OK,
                    {"status": "success", "proposal": proposal},
                )
                return
            self._error(HTTPStatus.NOT_FOUND, "NOT_FOUND", "Endpoint not found.")
        except (json.JSONDecodeError, UnicodeError, ValueError):
            self._error(
                HTTPStatus.BAD_REQUEST,
                "INVALID_JSON",
                "Request body must be valid JSON.",
            )
        except SettingsError as exc:
            self._error(HTTPStatus.BAD_REQUEST, exc.code, exc.safe_message)
        except PermissionError as exc:
            self._error(HTTPStatus.FORBIDDEN, "ORIGIN_DENIED", str(exc))
        except (AgentRuntimeError, ModelAdapterError) as exc:
            self._error(HTTPStatus.BAD_GATEWAY, exc.code, exc.safe_message)
        except Exception:
            self._error(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                "INTERNAL_ERROR",
                "The request could not be completed.",
            )

    def _read_json(self) -> dict[str, Any]:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            raise ValueError("invalid content length") from None
        if length <= 0 or length > MAX_REQUEST_BODY:
            raise ValueError("invalid request size")
        content_type = self.headers.get("Content-Type", "")
        if "application/json" not in content_type.lower():
            raise ValueError("JSON content type required")
        raw_body = self.rfile.read(length)
        self._require_same_origin()
        document = json.loads(raw_body.decode("utf-8"))
        if not isinstance(document, dict):
            raise ValueError("JSON object required")
        return document

    def _require_same_origin(self) -> None:
        if self.headers.get("X-SAVVA-Request") != "1":
            raise PermissionError("Missing same-origin request marker.")
        host = self.headers.get("Host", "").strip()
        allowed_hosts = (
            host.startswith("127.0.0.1:")
            or host == "127.0.0.1"
            or host.startswith("localhost:")
            or host == "localhost"
            or host.startswith("[::1]:")
        )
        if not allowed_hosts:
            raise PermissionError("Request host is not a local SAVVA origin.")
        origin = self.headers.get("Origin")
        if origin:
            parsed = urlparse(origin)
            if parsed.scheme != "http" or parsed.netloc != host:
                raise PermissionError("Request origin does not match the local backend.")

    def _json(self, status: HTTPStatus, document: dict[str, Any]) -> None:
        payload = json.dumps(document, ensure_ascii=False, allow_nan=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self._security_headers()
        self.end_headers()
        self.wfile.write(payload)

    def _error(self, status: HTTPStatus, code: str, message: str) -> None:
        self._json(
            status,
            {
                "status": "error",
                "error": {"code": code, "message": message},
            },
        )

    def _static(self, filename: str, content_type: str) -> None:
        path = self.web_root / filename
        try:
            payload = path.read_bytes()
        except OSError:
            self._error(HTTPStatus.NOT_FOUND, "NOT_FOUND", "Resource not found.")
            return
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-cache")
        self._security_headers()
        self.end_headers()
        self.wfile.write(payload)

    def _security_headers(self) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cache-Control", "no-store")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; "
            "base-uri 'none'; form-action 'self'",
        )

    def log_message(self, format: str, *args: Any) -> None:
        return


def create_server(host: str, port: int) -> SavvaHTTPServer:
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("SAVVA web interface may bind only to a loopback address.")
    server = SavvaHTTPServer((host, port), SavvaRequestHandler)
    server.runtime = AppRuntime()
    return server


def serve(host: str = "127.0.0.1", port: int = 8765) -> int:
    server = create_server(host, port)
    runtime = server.runtime
    print(f"SAVVA interface: http://{host}:{port}")
    print(
        "OpenRouter key: "
        + ("configured" if runtime.api_key() else "not configured")
    )
    print(f"Workspace: {runtime.settings().workspace_root}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(serve())
