"""Fixed-argument, bounded process execution for the SAVVA Candidate worktree."""

from __future__ import annotations

import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

from ..core.registry import ToolExecutionError
from .selfdev import MAX_TOOL_OUTPUT, SelfDevManifest


COMMANDS: dict[str, tuple[list[str], float]] = {
    "python.unittest": ([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"], 240),
    "python.syntax": ([sys.executable, "-m", "compileall", "-q", "savva", "tests"], 120),
    "selfdev.quality": ([sys.executable, "-m", "savva.selfdev_checks"], 30),
    "git.status": (["git", "-c", "core.fsmonitor=false", "status", "--short", "--branch"], 15),
    "git.diff": (["git", "diff", "--no-ext-diff", "--no-textconv", "HEAD", "--"], 20),
    "git.diff_check": (["git", "diff", "--no-ext-diff", "--no-textconv", "--check"], 20),
    "diagnostics.python_version": ([sys.executable, "--version"], 10),
    "diagnostics.git_root": (["git", "rev-parse", "--show-toplevel"], 10),
}
_SECRET_PATTERNS = tuple(re.compile(value) for value in (
    r"\bsk-or-v1-[A-Za-z0-9_-]{16,}\b", r"\bsk-[A-Za-z0-9_-]{20,}\b",
    r"\bgh[pousr]_[A-Za-z0-9]{20,}\b", r"\bAKIA[0-9A-Z]{16}\b",
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----"))


class ControlledWindowsExecutor:
    """Run a named recipe; no user-controlled text reaches an executable or shell."""

    def __init__(self, manifest: SelfDevManifest, *, output_limit: int = 12_000):
        self.manifest = manifest
        self.output_limit = min(max(int(output_limit), 1024), MAX_TOOL_OUTPUT)
        self.lock_path = manifest.root / ".savva" / "selfdev-executor.lock"

    def run(self, command_id: str) -> dict[str, Any]:
        if command_id not in COMMANDS or command_id not in self.manifest.document.get("check_commands", []):
            raise ToolExecutionError("EXECUTOR_COMMAND_DENIED", "This command is not in the Candidate allowlist.")
        argv, timeout = COMMANDS[command_id]
        root = self.manifest.root
        self.manifest._validate_root()
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        with self.lock_path.open("a+b") as lock_stream:
            if not _lock_file(lock_stream):
                raise ToolExecutionError("EXECUTOR_BUSY", "Another Candidate validation is already running.")
            started = time.perf_counter()
            try:
                result = _run_bounded(argv, root, _safe_environment(root), timeout, self.output_limit)
                result.update({"command_id": command_id,
                               "duration_ms": round((time.perf_counter() - started) * 1000)})
                for key in ("stdout", "stderr"):
                    result[key] = result[key].replace(str(root), "<candidate>").replace(str(root).replace("\\", "/"), "<candidate>")
                if command_id == "git.diff" and any(pattern.search(result["stdout"]) for pattern in _SECRET_PATTERNS):
                    result["stdout"] = "Secret-like content detected; diff withheld."
                    result["secret_scan"] = "BLOCKED"
                return result
            finally:
                _unlock_file(lock_stream)


def _run_bounded(argv: list[str], root: Path, env: dict[str, str], timeout: float,
                 output_limit: int) -> dict[str, Any]:
    try:
        process = subprocess.Popen(argv, cwd=root, shell=False, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0)
    except OSError as exc:
        return {"exit_code": None, "timed_out": False, "stdout": "",
                "stderr": f"Could not start the approved command ({type(exc).__name__}).",
                "output_truncated": False, "output_limit_exceeded": False}
    output = bytearray()
    overflow = threading.Event()

    def drain() -> None:
        assert process.stdout is not None
        while True:
            chunk = process.stdout.read(4096)
            if not chunk:
                return
            remaining = output_limit - len(output)
            if remaining > 0:
                output.extend(chunk[:remaining])
            if len(chunk) > remaining:
                overflow.set()
                _terminate_tree(process)

    reader = threading.Thread(target=drain, daemon=True, name="savva-selfdev-output")
    reader.start()
    timed_out = False
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        _terminate_tree(process)
        process.wait(timeout=5)
    reader.join(timeout=5)
    if process.stdout:
        process.stdout.close()
    text = bytes(output).decode("utf-8", errors="replace")
    if overflow.is_set():
        text += "\n[output limit reached; process terminated]"
    return {"exit_code": process.returncode if not timed_out else None,
            "timed_out": timed_out, "stdout": text, "stderr": "",
            "output_truncated": overflow.is_set(), "output_limit_exceeded": overflow.is_set()}


def _terminate_tree(process: subprocess.Popen) -> None:
    if os.name == "nt":
        try:
            subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                shell=False, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, timeout=5, check=False)
        except (OSError, subprocess.TimeoutExpired):
            process.kill()
    else:
        process.kill()


def _safe_environment(root: Path) -> dict[str, str]:
    env = {name: os.environ[name] for name in ("PATH", "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT") if name in os.environ}
    temp = root / ".savva" / "tmp"
    temp.mkdir(parents=True, exist_ok=True)
    env["TEMP"] = env["TMP"] = str(temp)
    env["LOCALAPPDATA"] = str(root / ".savva" / "localappdata")
    env["APPDATA"] = str(root / ".savva" / "appdata")
    return env


def _lock_file(stream) -> bool:
    stream.seek(0, os.SEEK_END)
    if stream.tell() == 0:
        stream.write(b"0")
        stream.flush()
    stream.seek(0)
    try:
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


def _unlock_file(stream) -> None:
    stream.seek(0)
    if os.name == "nt":
        import msvcrt
        msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl
        fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
