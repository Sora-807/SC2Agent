"""MemoryWorkspace:纯内存文件契约工作区。

文档通过 Engine.register_documents 或 add_document 注册进内存;
只存在注册过的文件,没有目录实体(目录从文件路径推导)。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .workspace import Workspace, WorkspaceConfig, line_info, join_lines, normalize_path


@dataclass
class MemoryFile:
    lines: list[str] = field(default_factory=list)
    version: int = 0


class MemoryWorkspace(Workspace):
    """文件集合存储在内存中;写操作递增文件版本,供观察策略做变更检测。"""

    def __init__(self, config: WorkspaceConfig | None = None) -> None:
        super().__init__(config)
        self._files: dict[str, MemoryFile] = {}
        self._version_counter = 0

    # ---- 存储原语 ----
    def _file_exists(self, path: str) -> bool:
        return path in self._files

    def _read_file(self, path: str) -> str:
        if path not in self._files:
            raise KeyError(path)
        return join_lines(self._files[path].lines)

    def _write_file(self, path: str, content: str) -> None:
        path = normalize_path(path)
        if not path:
            raise ValueError("path 不能为空")
        lines, _ = line_info(content)
        self._files[path] = MemoryFile(lines=lines, version=self._next_version())

    def _list_file_paths(self, prefix: str = "") -> list[str]:
        prefix = normalize_path(prefix)
        if prefix:
            prefix += "/"
        return sorted(path for path in self._files if not prefix or path.startswith(prefix))

    def _current_version(self, path: str) -> str | None:
        file = self._files.get(path)
        return str(file.version) if file else None

    def _next_version(self) -> int:
        self._version_counter += 1
        return self._version_counter

    # ---- 便捷方法 ----
    def add_document(self, path: str, content: str) -> None:
        """注册/刷新文档,并记录观察版本。"""
        super().add_document(path, content)

    def line_count(self, path: str) -> int:
        return len(self._files[path].lines)
