"""Outbound privacy filter for data sent to model providers."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

SENSITIVE_KEYS = re.compile(
    r"(?:password|passwd|secret|token|api[_-]?key|authorization|cookie|"
    r"private[_-]?key|session[_-]?key|credential)",
    re.IGNORECASE,
)
PRIVATE_KEY = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
    re.DOTALL,
)
BEARER_TOKEN = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+")
OPENROUTER_KEY = re.compile(r"\bsk-or-v1-[A-Za-z0-9_-]{16,}\b")
OPENAI_KEY = re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b")
KEY_VALUE = re.compile(
    r"(?i)\b(api[_-]?key|access[_-]?token|secret|password)\s*[:=]\s*"
    r"([^\s,;]+)"
)
EMAIL = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
WINDOWS_USER_PATH = re.compile(r"([A-Za-z]:\\Users\\)([^\\\s\"']+)")
UNIX_USER_PATH = re.compile(r"(/home/)([^/\s\"']+)")
UNSAFE_COMMAND = re.compile(
    r"(?i)(\bnpm\s+(?:install|i|create|ci|run)\b|\bnpx\b|\bpnpm\b|"
    r"\byarn\s+\S+|\bpip\s+install\b|\bwinget\s+install\b|"
    r"\bchoco\s+install\b|\bgit\s+clone\b|\bcd\b|"
    r"\bcode\s+[A-Za-z0-9_.\\/-]+|\bterminal\b|"
    r"\b(?:run|execute)\b.*\b(?:command|commands|terminal)\b|"
    r"\bcreate[-\u2011]react[-\u2011]app\b|\bscaffold\b|"
    r"\bcreate\s+(?:a\s+)?(?:new\s+)?react\s+(?:app|project)\b|"
    r"\b(?:start|launch)\s+(?:the\s+)?(?:development|dev)\s+server\b|"
    r"\binstall\s+(?:typescript|dependencies|packages|components)\b|"
    r"\binstall\b|\blocalhost\b|\bset\s+up\s+.*\bproject\b)"
)
UNSAFE_ANSWER_LINE = re.compile(
    r"(?i)(\b(?:созда\w*|установить|установка|запустить|запуск|"
    r"терминал\w*|команд\w*|выполните)\b|\bоткрыть\b|"
    r"\bоткр\w*|\bприступ\w*|\bподключ\w*|"
    r"что можно сделать|следующий шаг)"
)

def redact_text(value: str, workspace_root: str | Path | None = None) -> str:
    text = str(value)
    if workspace_root:
        root_text = str(Path(workspace_root))
        text = text.replace(root_text, "<workspace>")
        text = text.replace(root_text.replace("\\", "/"), "<workspace>")
    text = PRIVATE_KEY.sub("<redacted-private-key>", text)
    text = BEARER_TOKEN.sub("Bearer <redacted>", text)
    text = OPENROUTER_KEY.sub("<redacted-api-key>", text)
    text = OPENAI_KEY.sub("<redacted-api-key>", text)
    text = KEY_VALUE.sub(lambda match: f"{match.group(1)}=<redacted>", text)
    text = EMAIL.sub("<redacted-email>", text)
    text = WINDOWS_USER_PATH.sub(r"\1<user>", text)
    text = UNIX_USER_PATH.sub(r"\1<user>", text)
    return text


def sanitize_for_model(
    value: Any,
    workspace_root: str | Path | None = None,
    depth: int = 0,
) -> Any:
    if depth > 30:
        return "<redacted-depth-limit>"
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in value.items():
            clean_key = redact_text(str(key), workspace_root)[:200]
            if SENSITIVE_KEYS.search(str(key)):
                result[clean_key] = "<redacted>"
            else:
                result[clean_key] = sanitize_for_model(item, workspace_root, depth + 1)
        return result
    if isinstance(value, (list, tuple)):
        return [sanitize_for_model(item, workspace_root, depth + 1) for item in value]
    if isinstance(value, str):
        return redact_text(value, workspace_root)
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return redact_text(str(value), workspace_root)


def constrain_read_only_answer(answer: str) -> str:
    """Remove executable instructions from a read-only agent answer."""

    kept: list[str] = []
    in_fence = False
    for line in str(answer).splitlines():
        if line.strip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        if UNSAFE_COMMAND.search(line) or UNSAFE_ANSWER_LINE.search(line):
            continue
        kept.append(line)
    cleaned = "\n".join(kept).strip()
    return cleaned
