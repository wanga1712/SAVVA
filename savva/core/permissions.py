"""Permission model for registered SAVVA tools."""

from __future__ import annotations

from enum import StrEnum


class Permission(StrEnum):
    """Permissions understood by the registry.

    Read permissions and confirmation-gated ``files.write`` are implemented.
    Package installation and process execution remain reserved.
    """

    SYSTEM_READ = "system.read"
    WORKSPACE_READ = "workspace.read"
    FILES_WRITE = "files.write"
    PACKAGES_INSTALL = "packages.install"
    PROCESS_EXECUTE = "process.execute"
    SELFDEV_VALIDATE = "selfdev.validate"


READ_PERMISSIONS = frozenset(
    {Permission.SYSTEM_READ, Permission.WORKSPACE_READ}
)
IMPLEMENTED_PERMISSIONS = frozenset({*READ_PERMISSIONS, Permission.FILES_WRITE, Permission.SELFDEV_VALIDATE})
FUTURE_PERMISSIONS = frozenset(
    {
        Permission.PACKAGES_INSTALL,
        Permission.PROCESS_EXECUTE,
    }
)
READ_ONLY_RISK_LEVEL = "read_only"
WRITE_RISK_LEVEL = "write"
IMPLEMENTED_RISK_LEVELS = frozenset({READ_ONLY_RISK_LEVEL, WRITE_RISK_LEVEL})


class PermissionPolicy:
    """Explicit allow-list checked by the registry before execution."""

    def __init__(self, granted: set[Permission] | frozenset[Permission] | None = None):
        self._granted = frozenset(granted) if granted is not None else READ_PERMISSIONS

    @property
    def granted(self) -> frozenset[Permission]:
        return self._granted

    def missing(self, required: tuple[Permission, ...] | list[Permission]) -> list[str]:
        requested = {Permission(item) for item in required}
        return sorted(permission.value for permission in requested - self._granted)

    def allows(self, required: tuple[Permission, ...] | list[Permission]) -> bool:
        return not self.missing(required)
