"""Read-only Tailscale exit-node inspection for model egress enforcement."""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class TailscaleExitStatus:
    installed: bool
    backend_state: str | None
    exit_node_id: str | None
    exit_node_hostname: str | None
    exit_node_dns_name: str | None
    exit_node_online: bool
    exit_node_addresses: tuple[str, ...]
    ready: bool
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        document = asdict(self)
        document["exit_node_addresses"] = list(self.exit_node_addresses)
        return document


class TailscaleInspector:
    """Inspect the current route without changing Tailscale or Windows settings."""

    def inspect(self) -> TailscaleExitStatus:
        executable = self._find_executable()
        if executable is None:
            return TailscaleExitStatus(
                installed=False,
                backend_state=None,
                exit_node_id=None,
                exit_node_hostname=None,
                exit_node_dns_name=None,
                exit_node_online=False,
                exit_node_addresses=(),
                ready=False,
                error="Tailscale CLI was not found.",
            )
        try:
            completed = subprocess.run(
                [executable, "status", "--json"],
                shell=False,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=5,
                check=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except subprocess.TimeoutExpired:
            return self._error("Tailscale status check timed out.")
        except OSError:
            return self._error("Tailscale status could not be read.")
        if completed.returncode != 0:
            return self._error("Tailscale is not ready.")
        try:
            document = json.loads(completed.stdout)
        except json.JSONDecodeError:
            return self._error("Tailscale returned invalid status data.")
        if not isinstance(document, dict):
            return self._error("Tailscale returned invalid status data.")
        return self.parse_status(document)

    @classmethod
    def parse_status(cls, document: dict[str, Any]) -> TailscaleExitStatus:
        backend_state = document.get("BackendState")
        current = document.get("ExitNodeStatus")
        current = current if isinstance(current, dict) else {}
        peers = document.get("Peer")
        peers = peers if isinstance(peers, dict) else {}
        current_id = current.get("ID")
        peer: dict[str, Any] = {}
        if isinstance(current_id, str) and isinstance(peers.get(current_id), dict):
            peer = peers[current_id]
        if not peer and current_id:
            peer = next(
                (
                    candidate
                    for candidate in peers.values()
                    if isinstance(candidate, dict)
                    and candidate.get("ID") == current_id
                ),
                {},
            )
        hostname = peer.get("HostName")
        dns_name = peer.get("DNSName")
        addresses = peer.get("TailscaleIPs")
        addresses = (
            tuple(str(item) for item in addresses)
            if isinstance(addresses, list)
            else ()
        )
        online = bool(current.get("Online") or peer.get("Online"))
        ready = (
            backend_state == "Running"
            and bool(current_id)
            and online
        )
        return TailscaleExitStatus(
            installed=True,
            backend_state=str(backend_state) if backend_state else None,
            exit_node_id=str(current_id) if current_id else None,
            exit_node_hostname=str(hostname) if hostname else None,
            exit_node_dns_name=str(dns_name).rstrip(".") if dns_name else None,
            exit_node_online=online,
            exit_node_addresses=addresses,
            ready=ready,
            error=None if ready else "No online Tailscale exit node is selected.",
        )

    @staticmethod
    def _find_executable() -> str | None:
        discovered = shutil.which("tailscale")
        if discovered:
            return discovered
        windows_path = Path(r"C:\Program Files\Tailscale\tailscale.exe")
        return str(windows_path) if windows_path.is_file() else None

    @staticmethod
    def _error(message: str) -> TailscaleExitStatus:
        return TailscaleExitStatus(
            installed=True,
            backend_state=None,
            exit_node_id=None,
            exit_node_hostname=None,
            exit_node_dns_name=None,
            exit_node_online=False,
            exit_node_addresses=(),
            ready=False,
            error=message,
        )
