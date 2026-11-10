"""Permission model for registered SAVVA tools."""

from __future__ import annotations

from enum import StrEnum


class Permission(StrEnum):
    """Permissions understood by the registry.

    Only ``SYSTEM_READ`` is executable in WIP-SAVVA-001. The remaining values
    reserve stable names for a later permission-aware executor.
    """

    SYSTEM_READ = "system.read"
    FILES_WRITE = "files.write"
    PACKAGES_INSTALL = "packages.install"
    PROCESS_EXECUTE = "process.execute"


IMPLEMENTED_PERMISSIONS = frozenset({Permission.SYSTEM_READ})
FUTURE_PERMISSIONS = frozenset(
    {
        Permission.FILES_WRITE,
        Permission.PACKAGES_INSTALL,
        Permission.PROCESS_EXECUTE,
    }
)
READ_ONLY_RISK_LEVEL = "read_only"


class PermissionPolicy:
    """Explicit allow-list checked by the registry before execution."""

    def __init__(self, granted: set[Permission] | frozenset[Permission] | None = None):
        self._granted = frozenset(granted) if granted is not None else IMPLEMENTED_PERMISSIONS

    @property
    def granted(self) -> frozenset[Permission]:
        return self._granted

    def missing(self, required: tuple[Permission, ...] | list[Permission]) -> list[str]:
        requested = {Permission(item) for item in required}
        return sorted(permission.value for permission in requested - self._granted)

    def allows(self, required: tuple[Permission, ...] | list[Permission]) -> bool:
        return not self.missing(required)
