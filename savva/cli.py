"""Command-line interface for read-only SAVVA diagnostics."""

from __future__ import annotations

import argparse
import json
import time
from typing import Any, Sequence

from .agent.selfdev import SelfDevManifest
from .agent.selfdev_executor import COMMANDS, ControlledWindowsExecutor
from .tools import build_default_registry
from .agent.workflow import WorkflowStore


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="savva",
        description="SAVVA read-only system and development diagnostics.",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    doctor = commands.add_parser("doctor", help="Run safe environment diagnostics.")
    doctor.add_argument(
        "--json",
        action="store_true",
        help="Emit structured JSON instead of a human-readable summary.",
    )
    doctor.add_argument(
        "--project",
        metavar="DIRECTORY",
        help="Also compare dependencies declared in an existing project directory.",
    )

    tools = commands.add_parser("tools", help="Inspect the registered tool catalog.")
    tool_commands = tools.add_subparsers(dest="tools_command", required=True)
    tool_commands.add_parser("list", help="List registered tools without executing them.")
    inspect = tool_commands.add_parser("inspect", help="Show one tool contract.")
    inspect.add_argument("name", help="Registered tool name, for example system.summary.")

    serve = commands.add_parser("serve", help="Start the local SAVVA web interface.")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8765)

    selfdev = commands.add_parser("selfdev", help="Run safe tools only in the SAVVA Candidate worktree.")
    selfdev_commands = selfdev.add_subparsers(dest="selfdev_command", required=True)
    selfdev_serve = selfdev_commands.add_parser("serve", help="Start the isolated Candidate chat and approval UI.")
    selfdev_serve.add_argument("--host", default="127.0.0.1")
    selfdev_serve.add_argument("--port", type=int, default=8766)
    selfdev_check = selfdev_commands.add_parser("check", help="Run one fixed, manifest-approved Candidate check.")
    selfdev_check.add_argument("check_id", choices=sorted(COMMANDS))
    selfdev_status = selfdev_commands.add_parser("status", help="Read one persisted self-development WIP.")
    selfdev_status.add_argument("task_id")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.command == "serve":
        from .server import serve

        return serve(args.host, args.port)

    if args.command == "selfdev":
        try:
            manifest = SelfDevManifest.load()
            if args.selfdev_command == "serve":
                from .server import serve
                return serve(args.host, args.port, manifest.path)
            if args.selfdev_command == "check":
                result = ControlledWindowsExecutor(manifest).run(args.check_id)
                print(json.dumps(result, indent=2, ensure_ascii=False))
                return 0 if result["exit_code"] == 0 and not result["timed_out"] else 1
            store = WorkflowStore(manifest.root / ".savva" / "selfdev" / "tasks.json")
            task = store.get(args.task_id)
            if task is None:
                print(json.dumps({"status": "not_found"}))
                return 2
            print(json.dumps({"task_id": task.task_id, "state": task.state,
                              "request": task.request, "history": task.history,
                              "wip": task.metadata}, indent=2, ensure_ascii=False))
            return 0
        except (OSError, ValueError) as exc:
            print(json.dumps({"status": "error", "message": str(exc)}, ensure_ascii=False))
            return 1

    registry = build_default_registry()

    if args.command == "tools":
        if args.tools_command == "list":
            _print_tool_list(registry.list_tools())
            return 0
        try:
            contract = registry.get_contract(args.name)
        except KeyError:
            print(f"Unknown tool: {args.name}")
            return 2
        print(json.dumps(contract, indent=2, ensure_ascii=False))
        return 0

    started = time.perf_counter()
    system_parameters: dict[str, Any] = {}
    development_parameters = (
        {"project_path": args.project} if args.project else {}
    )
    system_result = registry.invoke("system.summary", system_parameters)
    development_result = registry.invoke(
        "development.inspect", development_parameters
    )
    duration_ms = round((time.perf_counter() - started) * 1000)
    results = [system_result, development_result]
    success = all(result.status == "success" for result in results)

    if args.json:
        payload = {
            "status": "success" if success else "error",
            "checks": [result.to_dict() for result in results],
            "duration_ms": duration_ms,
        }
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    elif success:
        _print_doctor(system_result.data or {}, development_result.data or {}, duration_ms)
    else:
        for result in results:
            if result.error:
                print(f"{result.tool}: {result.error['code']} - {result.error['message']}")
    return 0 if success else 1


def _print_tool_list(contracts: list[dict[str, Any]]) -> None:
    print(f"Registered tools: {len(contracts)}")
    for contract in contracts:
        permissions = ", ".join(contract["permissions"])
        print(
            f"{contract['name']:<24} {contract['risk_level']:<10} "
            f"{contract['timeout_seconds']:>4}s  {permissions}"
        )
        print(f"  {contract['description']}")


def _print_doctor(
    system: dict[str, Any],
    development: dict[str, Any],
    duration_ms: int,
) -> None:
    os_info = system["os"]
    cpu = system["cpu"]
    memory = system["memory"]
    print("SAVVA Doctor")
    print(
        f"OS: {os_info['name']} {os_info['release']} "
        f"({os_info['architecture']}, build {os_info['build']})"
    )
    print(
        f"CPU: {cpu['model'] or 'unknown'}; "
        f"cores={cpu['physical_cores'] or '?'}; threads={cpu['threads'] or '?'}"
    )
    print(
        f"RAM: {_format_bytes(memory['total_bytes'])} total, "
        f"{_format_bytes(memory['available_bytes'])} available"
    )
    if system["gpus"]:
        print("GPU: " + ", ".join(item["name"] for item in system["gpus"]))
    else:
        print("GPU: not detected through the safe registry probe")

    print("\nDisks:")
    for disk in system["disks"]:
        print(
            f"  {disk['device']}: {_format_bytes(disk['total_bytes'])} total, "
            f"{_format_bytes(disk['free_bytes'])} free"
        )

    print("\nSoftware:")
    groups = (
        ("Core", development["core_tools"]),
        ("Web", development["web_tools"]),
        ("Other", development["other_tools"]),
    )
    for title, tools in groups:
        print(f"  {title}:")
        for tool in tools.values():
            if tool["installed"]:
                version = tool["version"] or "version unknown"
                print(f"    [OK] {tool['name']}: {version}")
            else:
                print(f"    [--] {tool['name']}: not found")

    readiness = development["readiness"]
    print(
        f"\nReact + TypeScript readiness: {readiness['status'].upper()}"
    )
    for note in readiness["report"]:
        print(f"  - {note}")
    if readiness["missing_required"]:
        print("  - Missing required: " + ", ".join(readiness["missing_required"]))
    if readiness["missing_recommended"]:
        print(
            "  - Optional tools not detected: "
            + ", ".join(readiness["missing_recommended"])
        )

    project = development.get("project")
    if project:
        print(f"\nProject: {project['path']}")
        print(
            f"  Declared dependencies: {len(project['declared_dependencies'])}; "
            f"installed: {len(project['installed_dependencies'])}; "
            f"missing: {len(project['missing_dependencies'])}"
        )
    print(f"\nDiagnostics duration: {duration_ms} ms")


def _format_bytes(value: int | None) -> str:
    if value is None:
        return "unknown"
    size = float(value)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if size < 1024 or unit == "TiB":
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TiB"


if __name__ == "__main__":
    raise SystemExit(main())
