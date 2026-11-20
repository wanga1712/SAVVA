"""Provider, budget, and workspace settings without secret persistence."""

from __future__ import annotations

import json
import os
import threading
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

DEFAULT_OPENROUTER_MODEL = "nvidia/nemotron-3.5-lightning:free"
DEFAULT_OLLAMA_MODEL = "qwen/qwen3.5-9b"
DEFAULT_OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_OLLAMA_BASE_URL = "http://127.0.0.1:11434/v1"


class SettingsError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.safe_message = message


@dataclass(frozen=True, slots=True)
class AppSettings:
    provider: str = "openrouter"
    model: str = DEFAULT_OPENROUTER_MODEL
    base_url: str = DEFAULT_OPENROUTER_BASE_URL
    max_tokens: int = 2048
    max_tool_steps: int = 6
    max_requests_per_session: int = 12
    max_request_cost_usd: float = 0.05
    max_session_cost_usd: float = 1.0
    max_duration_seconds: float = 120.0
    workspace_root: str = ""
    codex_model: str = ""
    failover_daily_budget_usd: float = 3.0
    failover_enabled: bool = True
    reasoning_effort: str = "low"
    require_tailscale_exit_node: bool = False
    expected_exit_node: str = ""

    def validate(self) -> None:
        if self.provider not in {"openrouter", "ollama"}:
            raise SettingsError("INVALID_SETTINGS", "Provider must be openrouter or ollama.")
        if not self.model.strip() or len(self.model) > 200:
            raise SettingsError("INVALID_SETTINGS", "Model identifier is invalid.")
        if not 64 <= self.max_tokens <= 65536:
            raise SettingsError("INVALID_SETTINGS", "max_tokens must be between 64 and 65536.")
        if not 1 <= self.max_tool_steps <= 20:
            raise SettingsError("INVALID_SETTINGS", "max_tool_steps must be between 1 and 20.")
        if not 1 <= self.max_requests_per_session <= 100:
            raise SettingsError(
                "INVALID_SETTINGS",
                "max_requests_per_session must be between 1 and 100.",
            )
        if not 0 <= self.max_request_cost_usd <= 1000:
            raise SettingsError("INVALID_SETTINGS", "Request cost limit is invalid.")
        if not 0 <= self.max_session_cost_usd <= 10000:
            raise SettingsError("INVALID_SETTINGS", "Session cost limit is invalid.")
        if not 0 <= self.failover_daily_budget_usd <= 10000:
            raise SettingsError("INVALID_SETTINGS", "Failover daily cost limit is invalid.")
        if len(self.codex_model) > 200:
            raise SettingsError("INVALID_SETTINGS", "Codex model identifier is invalid.")
        if not 5 <= self.max_duration_seconds <= 600:
            raise SettingsError(
                "INVALID_SETTINGS",
                "max_duration_seconds must be between 5 and 600.",
            )
        if self.reasoning_effort not in {"minimal", "low", "medium", "high", "max"}:
            raise SettingsError("INVALID_SETTINGS", "Reasoning effort is invalid.")
        if len(self.expected_exit_node) > 253:
            raise SettingsError("INVALID_SETTINGS", "Expected exit node is too long.")
        parsed = urlparse(self.base_url)
        if self.provider == "openrouter":
            if parsed.scheme != "https" or parsed.hostname != "openrouter.ai":
                raise SettingsError(
                    "INVALID_SETTINGS",
                    "OpenRouter base_url must use https://openrouter.ai.",
                )
        elif parsed.scheme not in {"http", "https"} or parsed.hostname not in {
            "127.0.0.1",
            "localhost",
            "::1",
        }:
            raise SettingsError(
                "INVALID_SETTINGS",
                "Ollama base_url must point to a loopback address.",
            )
        if not self.workspace_root:
            raise SettingsError("INVALID_SETTINGS", "Workspace root is required.")

    def to_public_dict(self) -> dict[str, Any]:
        return asdict(self)


def default_workspace_root(project_root: Path | None = None) -> Path:
    root = project_root or Path.cwd()
    return (root / "workspace").resolve()


def default_usage_log_path() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA")
    base = Path(local_app_data) if local_app_data else Path.home() / ".savva"
    return base / "SAVVA" / "usage.jsonl"


def default_settings_path() -> Path:
    return default_usage_log_path().with_name("settings.json")


def settings_from_environment(project_root: Path | None = None) -> AppSettings:
    provider = os.environ.get("SAVVA_PROVIDER", "openrouter").strip().lower()
    model = os.environ.get(
        "SAVVA_MODEL",
        DEFAULT_OPENROUTER_MODEL if provider == "openrouter" else DEFAULT_OLLAMA_MODEL,
    )
    base_url = os.environ.get(
        "SAVVA_BASE_URL",
        DEFAULT_OPENROUTER_BASE_URL if provider == "openrouter" else DEFAULT_OLLAMA_BASE_URL,
    )
    workspace = os.environ.get("SAVVA_WORKSPACE_ROOT") or str(
        default_workspace_root(project_root)
    )
    settings = AppSettings(
        provider=provider,
        model=model.strip(),
        base_url=base_url.strip(),
        max_tokens=_env_int("SAVVA_MAX_TOKENS", 2048),
        max_tool_steps=_env_int("SAVVA_MAX_TOOL_STEPS", 6),
        max_requests_per_session=_env_int("SAVVA_MAX_REQUESTS", 12),
        max_request_cost_usd=_env_float("SAVVA_MAX_REQUEST_COST", 0.05),
        max_session_cost_usd=_env_float("SAVVA_MAX_SESSION_COST", 1.0),
        max_duration_seconds=_env_float("SAVVA_MAX_DURATION_SECONDS", 120.0),
        codex_model=os.environ.get("SAVVA_CODEX_MODEL", "").strip(),
        failover_daily_budget_usd=_env_float("SAVVA_FAILOVER_DAILY_BUDGET_USD", 3.0),
        failover_enabled=_env_bool("SAVVA_FAILOVER_ENABLED", True),
        workspace_root=str(Path(workspace).expanduser().resolve()),
        reasoning_effort=os.environ.get("SAVVA_REASONING_EFFORT", "low").strip().lower(),
        require_tailscale_exit_node=_env_bool(
            "SAVVA_REQUIRE_TAILSCALE_EXIT_NODE", False
        ),
        expected_exit_node=os.environ.get("SAVVA_EXPECTED_EXIT_NODE", "").strip(),
    )
    settings.validate()
    return settings


class SettingsStore:
    """Thread-safe non-secret settings with optional local persistence."""

    def __init__(self, initial: AppSettings, path: Path | None = None):
        self._settings = initial
        self._path = path or default_settings_path()
        self._lock = threading.RLock()

    def load(self) -> None:
        try:
            if not self._path.is_file() or self._path.stat().st_size > 64_000:
                return
            document = json.loads(self._path.read_text(encoding="utf-8"))
            if isinstance(document, dict):
                current = self._settings.to_public_dict()
                current.update(
                    {
                        key: _coerce_setting(key, value)
                        for key, value in document.items()
                        if key in current
                    }
                )
                candidate = AppSettings(**current)
                candidate.validate()
                self._settings = candidate
        except (OSError, ValueError, TypeError):
            return

    def get(self) -> AppSettings:
        with self._lock:
            return self._settings

    def update(self, changes: dict[str, Any]) -> AppSettings:
        allowed = set(AppSettings.__dataclass_fields__)
        safe_changes = {
            key: _coerce_setting(key, value)
            for key, value in changes.items()
            if key in allowed
        }
        with self._lock:
            current = self._settings.to_public_dict()
            old_provider = self._settings.provider
            current.update(safe_changes)
            if "provider" in safe_changes and "base_url" not in safe_changes:
                current["base_url"] = (
                    DEFAULT_OPENROUTER_BASE_URL
                    if current["provider"] == "openrouter"
                    else DEFAULT_OLLAMA_BASE_URL
                )
                current["model"] = (
                    DEFAULT_OPENROUTER_MODEL
                    if current["provider"] == "openrouter"
                    else DEFAULT_OLLAMA_MODEL
                )
            elif old_provider != current["provider"] and not current["base_url"]:
                raise SettingsError("INVALID_SETTINGS", "Base URL is required.")
            candidate = AppSettings(**current)
            candidate.validate()
            workspace = Path(candidate.workspace_root).expanduser().resolve()
            workspace.mkdir(parents=True, exist_ok=True)
            if not workspace.is_dir():
                raise SettingsError(
                    "INVALID_WORKSPACE", "Workspace root must be a directory."
                )
            candidate = AppSettings(**{**candidate.to_public_dict(), "workspace_root": str(workspace)})
            self._settings = candidate
            self._persist(candidate)
            return candidate

    def _persist(self, settings: AppSettings) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self._path.with_suffix(".tmp")
            temporary.write_text(
                json.dumps(settings.to_public_dict(), indent=2),
                encoding="utf-8",
            )
            temporary.replace(self._path)
        except OSError:
            pass


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, str(default)))
    except ValueError:
        return default


def _env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _coerce_setting(key: str, value: Any) -> Any:
    if key in {
        "max_tokens",
        "max_tool_steps",
        "max_requests_per_session",
    }:
        return int(value)
    if key in {
        "max_request_cost_usd",
        "max_session_cost_usd",
        "max_duration_seconds",
        "failover_daily_budget_usd",
    }:
        return float(value)
    if key == "require_tailscale_exit_node":
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in {"1", "true", "yes", "on"}
    if key == "failover_enabled":
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in {"1", "true", "yes", "on"}
    return str(value).strip() if value is not None else ""
