"""Workspace:文件契约工作区(内存模式 / 磁盘模式 / 多 agent 权限视图)。"""
from .disk import DiskWorkspace
from .memory import MemoryWorkspace
from .scoped import ScopedWorkspace
from .workspace import (
    FileChangedError,
    FileEntry,
    FileNotObservedError,
    FileStat,
    GrepMatch,
    InvalidPathError,
    Workspace,
    WorkspaceConfig,
    WorkspaceError,
)

__all__ = [
    "Workspace",
    "WorkspaceConfig",
    "WorkspaceError",
    "FileChangedError",
    "FileNotObservedError",
    "InvalidPathError",
    "FileEntry",
    "FileStat",
    "GrepMatch",
    "MemoryWorkspace",
    "DiskWorkspace",
    "ScopedWorkspace",
]
