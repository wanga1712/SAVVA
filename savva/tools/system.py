"""Safe, read-only hardware and operating-system inspection."""

from __future__ import annotations

import ctypes
import os
import platform
import shutil
from typing import Any


class SystemInspector:
    """Collect a bounded system summary using OS APIs instead of disk scans."""

    def summary(self) -> dict[str, Any]:
        memory = self._get_memory()
        disks = self._get_disks()
        return {
            "os": self._get_os(),
            "cpu": self._get_cpu(),
            "memory": memory,
            "gpus": self._get_gpus(),
            "disks": disks,
            "disk_total_bytes": sum(item["total_bytes"] for item in disks),
            "disk_free_bytes": sum(item["free_bytes"] for item in disks),
        }

    def _get_os(self) -> dict[str, Any]:
        version = platform.version()
        release = platform.release()
        if os.name == "nt":
            try:
                build_number = int(version.rsplit(".", 1)[-1])
                if build_number >= 22000:
                    release = "11"
                elif build_number >= 10240:
                    release = "10"
            except ValueError:
                pass
        return {
            "name": platform.system() or "Unknown",
            "release": release or "Unknown",
            "version": version or "Unknown",
            "build": version.rsplit(".", 1)[-1] if version else None,
            "architecture": platform.machine() or "Unknown",
        }

    def _get_cpu(self) -> dict[str, Any]:
        models: list[str] = []
        physical_cores = 0
        logical_processors = 0
        if os.name == "nt":
            try:
                import winreg

                access = winreg.KEY_READ | getattr(winreg, "KEY_WOW64_64KEY", 0)
                with winreg.OpenKey(
                    winreg.HKEY_LOCAL_MACHINE,
                    r"HARDWARE\DESCRIPTION\System\CentralProcessor",
                    0,
                    access,
                ) as root:
                    index = 0
                    while True:
                        try:
                            with winreg.OpenKey(root, str(index)) as processor:
                                model = self._registry_value(processor, "ProcessorNameString")
                                cleaned_model = str(model).strip() if model else ""
                                if cleaned_model and cleaned_model not in models:
                                    models.append(cleaned_model)
                                physical_cores += int(
                                    self._registry_value(processor, "NumberOfCores") or 0
                                )
                                logical_processors += int(
                                    self._registry_value(processor, "NumberOfLogicalProcessors") or 0
                                )
                            index += 1
                        except OSError:
                            break
            except (ImportError, OSError, ValueError):
                pass

        if not models and platform.processor():
            models.append(platform.processor().strip())
        if not physical_cores and os.name == "nt":
            physical_cores = self._windows_physical_core_count()
        processor_count = os.cpu_count() or None
        return {
            "model": ", ".join(models) if models else None,
            "physical_cores": physical_cores or None,
            "logical_processors": logical_processors or processor_count,
            "threads": logical_processors or processor_count,
        }

    @staticmethod
    def _windows_physical_core_count() -> int:
        class LogicalProcessorInformation(ctypes.Structure):
            class Information(ctypes.Union):
                _fields_ = [("Reserved", ctypes.c_ulonglong * 2)]

            _anonymous_ = ("information",)
            _fields_ = [
                ("ProcessorMask", ctypes.c_size_t),
                ("Relationship", ctypes.c_ulong),
                ("information", Information),
            ]

        try:
            kernel32 = ctypes.windll.kernel32
            length = ctypes.c_ulong(0)
            kernel32.GetLogicalProcessorInformation(None, ctypes.byref(length))
            if not length.value:
                return 0
            entry_size = ctypes.sizeof(LogicalProcessorInformation)
            count = length.value // entry_size
            if not count:
                return 0
            buffer = (ctypes.c_byte * length.value)()
            if not kernel32.GetLogicalProcessorInformation(buffer, ctypes.byref(length)):
                return 0
            entries = (LogicalProcessorInformation * count).from_buffer(buffer)
            return sum(1 for entry in entries if entry.Relationship == 0)
        except (AttributeError, OSError, ValueError):
            return 0

    @staticmethod
    def _registry_value(key: Any, name: str) -> Any:
        import winreg

        try:
            return winreg.QueryValueEx(key, name)[0]
        except OSError:
            return None

    def _get_memory(self) -> dict[str, int | None]:
        if os.name == "nt":
            class MemoryStatus(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            status = MemoryStatus()
            status.dwLength = ctypes.sizeof(MemoryStatus)
            try:
                if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
                    return {
                        "total_bytes": int(status.ullTotalPhys),
                        "available_bytes": int(status.ullAvailPhys),
                    }
            except (AttributeError, OSError):
                pass
        return {"total_bytes": None, "available_bytes": None}

    def _get_gpus(self) -> list[dict[str, str]]:
        if os.name != "nt":
            return []
        found: list[dict[str, str]] = []
        try:
            import winreg

            access = winreg.KEY_READ | getattr(winreg, "KEY_WOW64_64KEY", 0)
            with winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}",
                0,
                access,
            ) as root:
                index = 0
                while True:
                    try:
                        with winreg.OpenKey(root, f"{index:04d}") as adapter:
                            name = self._registry_value(adapter, "DriverDesc")
                            if name and all(item["name"] != name for item in found):
                                found.append({"name": str(name).strip()})
                        index += 1
                    except OSError:
                        break
        except (ImportError, OSError):
            pass
        return found

    def _get_disks(self) -> list[dict[str, int | str]]:
        roots = self._disk_roots()
        disks: list[dict[str, int | str]] = []
        for root in roots:
            try:
                usage = shutil.disk_usage(root)
            except OSError:
                continue
            disks.append(
                {
                    "device": root,
                    "total_bytes": int(usage.total),
                    "free_bytes": int(usage.free),
                }
            )
        return disks

    @staticmethod
    def _disk_roots() -> list[str]:
        if os.name != "nt":
            return ["/"]
        buffer = ctypes.create_unicode_buffer(1024)
        try:
            length = ctypes.windll.kernel32.GetLogicalDriveStringsW(
                len(buffer), buffer
            )
        except (AttributeError, OSError):
            return []
        if not length:
            return []
        return [item for item in buffer[:length].split("\x00") if item]
