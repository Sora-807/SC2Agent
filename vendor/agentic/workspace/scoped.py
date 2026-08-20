"""ScopedWorkspace:多 agent 的权限视图。

把共享工作区(Workspace)按 Guard 归属包装成单个 agent 可见的视图:
- 读:默认全部可见;hidden agent 只可见自己区域与 grant 的文件。
- 写:仅 Guard 判定为归属自己的文件可写,越界直接 PermissionError。
- 观察策略由共享 Workspace 统一维护;本类只做权限过滤,不复制数据。
"""
from __future__ import annotations

from ..guard import Guard
from .workspace import FileEntry, FileStat, GrepMatch, Workspace


class ScopedWorkspace:
    """共享工作区的权限视图,与 Workspace 暴露相同的工具方法。"""

    def __init__(self, shared: Workspace, agent_target: str, guard: Guard, *, hidden: bool = False) -> None:
        self._shared = shared
        self._agent = agent_target
        self._guard = guard
        self._hidden = hidden
        self._contract = None

    def set_contract(self, contract) -> None:
        """设置文件契约;写权限在 Guard 之外再按契约约束。"""
        self._contract = contract

    @property
    def agent_target(self) -> str:
        return self._agent

    # ---- 权限 ----
    def mode(self, path: str) -> str:
        return "rw" if self._guard.can_write(self._agent, path) else "ro"

    def _can_see(self, path: str) -> bool:
        return self._guard.can_see(self._agent, path, self._hidden)

    def _deny_write(self, path: str) -> None:
        owner = self._guard.owner(path)
        raise PermissionError(
            f"{path} 不属于 '{self._agent}' 可写(当前归属 '{owner or 'main'}')"
        )

    def _check_write(self, path: str) -> None:
        if not self._guard.can_write(self._agent, path):
            self._deny_write(path)
        if not self._contract_allows_write(path):
            allowed = self._contract_write_prefixes()
            raise PermissionError(
                f"{path} 不在文件契约允许写入的范围 {allowed} 内"
            )

    def _contract_write_prefixes(self) -> list[str]:
        contract = self._contract
        if contract is None:
            return []
        prefixes: list[str] = []
        for value in (getattr(contract, "working_directory", ""), getattr(contract, "output_directory", "")):
            if value:
                prefix = value.rstrip("/")
                if prefix not in prefixes:
                    prefixes.append(prefix)
        return prefixes

    def _contract_allows_write(self, path: str) -> bool:
        prefixes = self._contract_write_prefixes()
        if not prefixes:
            return True  # 契约没有限制写目录,只按 Guard 归属
        normalized = path.rstrip("/")
        return any(normalized == prefix or normalized.startswith(prefix + "/") for prefix in prefixes)

    # ---- 读 ----
    def contains(self, path: str) -> bool:
        return self._can_see(path) and self._shared.contains(path)

    def __contains__(self, path: str) -> bool:
        return self.contains(path)

    def read_text(self, path: str) -> str:
        if not self._can_see(path):
            raise KeyError(path)
        return self._shared.read_text(path)

    def read(self, path: str) -> str:
        return self.read_text(path)

    def line_count(self, path: str) -> int:
        if not self._can_see(path):
            raise KeyError(path)
        return self._shared.line_count(path) if hasattr(self._shared, "line_count") else 0

    # ---- 写 ----
    def write_text(self, path: str, content: str) -> None:
        self._check_write(path)
        self._shared.write_text(path, content)

    def append_text(self, path: str, content: str) -> None:
        self._check_write(path)
        self._shared.append_text(path, content)

    def edit_text(self, path: str, old_string: str, new_string: str, replace_all: bool = False) -> int:
        self._check_write(path)
        return self._shared.edit_text(path, old_string, new_string, replace_all)

    def insert_text(self, path: str, line_number: int, content: str) -> None:
        self._check_write(path)
        self._shared.insert_text(path, line_number, content)

    def delete_lines(self, path: str, start_line: int, end_line: int) -> int:
        self._check_write(path)
        return self._shared.delete_lines(path, start_line, end_line)

    # ---- 发现 ----
    def list_entries(self, prefix: str = "") -> list[FileEntry]:
        visible_files = {p for p in self._shared.visible_paths() if self._can_see(p)}
        entries: list[FileEntry] = []
        directories: set[str] = set()
        for file_path in visible_files:
            if prefix and not file_path.startswith(prefix.rstrip("/") + "/") and file_path != prefix:
                continue
            entries.append(FileEntry(
                path=file_path,
                type="file",
                mode=self.mode(file_path),
                size_bytes=None,
                line_count=None,
            ))
            parts = file_path.split("/")
            for index in range(1, len(parts)):
                directory = "/".join(parts[:index])
                if not prefix or directory == prefix or directory.startswith(prefix.rstrip("/") + "/"):
                    directories.add(directory)
        for directory in sorted(directories):
            entries.append(FileEntry(path=directory, type="directory", mode="dir"))
        return sorted(entries, key=lambda entry: entry.path)

    def stat(self, path: str) -> FileStat:
        if not self._can_see(path):
            raise KeyError(path)
        return self._shared.stat(path)

    def glob(self, pattern: str, path: str = "") -> list[str]:
        return [p for p in self._shared.glob(pattern, path) if self._can_see(p)]

    def grep(self, pattern: str, path: str = "", include: str | None = None) -> list[GrepMatch]:
        return [m for m in self._shared.grep(pattern, path, include) if self._can_see(m.path)]

    def visible_paths(self) -> list[str]:
        return [p for p in self._shared.visible_paths() if self._can_see(p)]

    # ---- 产出 ----
    def workspace_out(self) -> dict[str, str]:
        """返回本 agent 归属(可写)的文件快照。"""
        result: dict[str, str] = {}
        for path in self._shared.visible_paths():
            if self._guard.can_write(self._agent, path):
                try:
                    result[path] = self._shared.read_text(path)
                except Exception:
                    continue
        return result

    def snapshot(self) -> dict[str, str]:
        return self.workspace_out()
