"""Offline-capable SAVVA emergency doctor and loopback status page."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any


def _http(url: str, timeout: float = 3.0, headers: dict[str, str] | None = None) -> dict[str, Any]:
    try:
        request = urllib.request.Request(url, headers=headers or {}, method="GET")
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return {"status": "available", "http_status": response.status}
    except urllib.error.HTTPError as exc:
        return {"status": "http_error", "http_status": exc.code}
    except (urllib.error.URLError, TimeoutError, OSError):
        return {"status": "unavailable"}


def _command(args: list[str], timeout: float = 5.0) -> dict[str, Any]:
    try:
        result = subprocess.run(args, stdin=subprocess.DEVNULL, capture_output=True,
                                text=True, encoding="utf-8", errors="replace", timeout=timeout,
                                check=False, shell=False,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.TimeoutExpired):
        return {"status": "unavailable"}
    return {"status": "available" if result.returncode == 0 else "error",
            "return_code": result.returncode,
            "detail": (result.stdout + result.stderr).strip()[:500] or None}


def _openrouter_key_check(key: str, source: str) -> dict[str, Any]:
    request = urllib.request.Request("https://openrouter.ai/api/v1/key",
                                     headers={"Authorization": f"Bearer {key}"}, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=3) as response:
            data = json.loads(response.read(256_000).decode("utf-8")).get("data", {})
            return {"status": "available", "http_status": response.status,
                    "key_configured": True, "key_source": source,
                    "limit": data.get("limit"), "limit_remaining": data.get("limit_remaining")}
    except urllib.error.HTTPError as exc:
        return {"status": "auth_failed" if exc.code in {401, 403} else "http_error",
                "http_status": exc.code, "key_configured": True, "key_source": source}
    except (urllib.error.URLError, TimeoutError, OSError, UnicodeError, json.JSONDecodeError, AttributeError):
        return {"status": "unavailable", "key_configured": True, "key_source": source}


def _codex_status() -> dict[str, Any]:
    executable = shutil.which("codex")
    if not executable:
        return {"cli": "missing", "login": "unknown", "quota": "unknown"}
    login = _command([executable, "login", "status"])
    logged_in = login["status"] == "available" and "Logged in using ChatGPT" in (login.get("detail") or "")
    result: dict[str, Any] = {"cli": "available", "login": "authenticated" if logged_in else "not_confirmed", "quota": "unknown"}
    if not logged_in:
        return result
    adapter = None
    try:
        from ..agent.codex_app_server import CodexAppServerAdapter
        adapter = CodexAppServerAdapter(workspace_root=os.getcwd(), timeout_seconds=12)
        snapshot = adapter.read_rate_limits()
        allowed = snapshot.get("ordinaryUsageAllowed")
        result["quota"] = "available" if allowed is True else "limited" if allowed is False else "unknown"
        result["quota_confirmed"] = allowed is not None
    except Exception as exc:
        result["quota"] = "unavailable"
        result["quota_error"] = getattr(exc, "code", "CODEX_STATUS_ERROR")
    finally:
        if adapter:
            adapter.close()
    return result


def doctor() -> dict[str, Any]:
    result: dict[str, Any] = {"component": "SAVVA Emergency Core", "mode": "deterministic", "checks": {}}
    result["checks"]["backend"] = _http("http://127.0.0.1:8765/api/health")
    result["checks"]["codex"] = _codex_status()
    key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    key_source = "environment" if key else None
    if not key:
        try:
            from ..agent.credentials import WindowsDPAPISecretStore
            key = WindowsDPAPISecretStore().get() or ""
            key_source = "windows_dpapi" if key else None
        except Exception:
            key = ""
    if key:
        result["checks"]["openrouter"] = _openrouter_key_check(key, key_source or "unknown")
    else:
        result["checks"]["openrouter"] = {"status": _http("https://openrouter.ai/api/v1/models")["status"], "key_configured": False}
    result["checks"]["local_llm"] = {"runtime": shutil.which("ollama") or "missing",
                                       "api": _http("http://127.0.0.1:11434/api/tags")["status"]}
    try:
        from ..agent.network import TailscaleInspector
        state = TailscaleInspector().inspect()
        result["checks"]["tailscale"] = {"installed": state.installed, "backend_state": state.backend_state,
                                           "exit_node_online": state.exit_node_online, "ready": state.ready}
    except Exception:
        result["checks"]["tailscale"] = {"status": "unknown"}
    return result


class EmergencyHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path == "/api/doctor":
            body = json.dumps(doctor(), ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
        elif self.path == "/":
            body = _PAGE.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
        else:
            body = b"Not found"
            self.send_response(404)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args: Any) -> None:
        # Do not emit request payloads or authorization data to the console.
        return


_PAGE = """<!doctype html><html lang='en'><meta charset='utf-8'><meta name='viewport' content='width=device-width'><title>SAVVA Emergency</title><style>body{font:16px system-ui;max-width:900px;margin:3rem auto;padding:0 1rem;background:#101522;color:#eef}button{padding:.7rem 1rem}pre{white-space:pre-wrap;background:#1c2638;padding:1rem;border-radius:8px}</style><h1>SAVVA Emergency Core</h1><p>Deterministic diagnostics; this is not an AI response.</p><button onclick='refresh()'>Run diagnostics</button><pre id='result'>Checking…</pre><script>async function refresh(){let e=document.querySelector('#result');e.textContent='Checking…';try{let r=await fetch('/api/doctor',{cache:'no-store'});e.textContent=JSON.stringify(await r.json(),null,2)}catch(_){e.textContent='Emergency diagnostics request failed.'}}refresh()</script></html>"""


def main() -> int:
    parser = argparse.ArgumentParser(description="SAVVA deterministic emergency diagnostics")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("doctor", help="Run local, bounded diagnostics")
    serve = sub.add_parser("serve", help="Serve standalone status page on loopback")
    serve.add_argument("--host", default="127.0.0.1", choices=("127.0.0.1", "localhost"))
    serve.add_argument("--port", type=int, default=8768)
    args = parser.parse_args()
    if args.command == "doctor":
        print(json.dumps(doctor(), indent=2, ensure_ascii=False))
        return 0
    if not 1024 <= args.port <= 65535:
        parser.error("port must be between 1024 and 65535")
    print(f"SAVVA Emergency Core: http://{args.host}:{args.port}/")
    HTTPServer((args.host, args.port), EmergencyHandler).serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
