"""DiskWorkspace:绑定磁盘根目录的文件契约工作区。

所有工具都使用相对于根目录的完整路径;不支持 cd,也看不到根目录外的内容。
路径校验采用"先规范化、再物理校验"的双重检查:
- 拒绝绝对路径与包含 .. 的路径。
- 解析真实路径(resolve,包含符号链接)后,必须位于根目录之内。
"""
from __future__ import annotations

import os
from pathlib import Path

from .workspace import (
    FileEntry,
    FileStat,
    Workspace,
    WorkspaceConfig,
    WorkspaceError,
    ignored_directory,
    is_absolute_path,
    line_info,
    normalize_path,
)


class DiskWorkspace(Workspace):
    """对磁盘根目录做物理隔离的文件工作区;根外路径完全不可见、不可读写。"""

    def __init__(self, root: str | Path, config: WorkspaceConfig | None = None) -> None:
        super().__init__(config)
        self.root = Path(root).resolve()
        if not self.root.exists():
            self.root.mkdir(parents=True, exist_ok=True)
        if not self.root.is_dir():
            raise ValueError(f"DiskWorkspace root 不是目录: {self.root}")

    # ---- 路径解析 ----
    def _resolve(self, path: str) -> Path:
        if is_absolute_path(path):
            raise WorkspaceError(f"非法路径 '{path}':DiskWorkspace 只接受相对根目录的路径")
        relative = normalize_path(path)
        candidate = self.root / relative if relative else self.root
        resolved = candidate.resolve()
        root_resolved = self.root.resolve()
        if resolved != root_resolved and not resolved.is_relative_to(root_resolved):
            raise WorkspaceError(f"路径越界,已拒绝访问根目录外的内容: {path}")
        return resolved

    def _relative(self, absolute: Path) -> str:
        return absolute.relative_to(self.root).as_posix()

    # ---- 存储原语 ----
    def _file_exists(self, path: str) -> bool:
        resolved = self._resolve(path)
        return resolved.is_file()

    def _read_file(self, path: str) -> str:
        resolved = self._resolve(path)
        if not resolved.exists():
            raise KeyError(path)
        if not resolved.is_file():
            raise WorkspaceError(f"{path} 不是普通文件")
        size = resolved.stat().st_size
        if size > self.config.max_read_bytes:
            raise WorkspaceError(
                f"{path} 大小为 {size} 字节,超过单文件读取上限 {self.config.max_read_bytes} 字节"
            )
        data = resolved.read_bytes()
        if b"\x00" in data[:8192]:
            raise WorkspaceError(f"{path} 疑似二进制文件,已拒绝读取")
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError as error:
            raise WorkspaceError(f"{path} 不是合法 UTF-8 文本,已拒绝读取") from error

    def _write_file(self, path: str, content: str) -> None:
        if is_absolute_path(path):
            raise WorkspaceError(f"非法路径 '{path}':DiskWorkspace 只接受相对根目录的路径")
        relative = normalize_path(path)
        if not relative:
            raise ValueError("path 不能为空")
        target = self.root / relative
        resolved = target.resolve()
        if resolved != self.root.resolve() and not resolved.is_relative_to(self.root.resolve()):
            raise WorkspaceError(f"路径越界,已拒绝写入根目录外的内容: {path}")
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f".{target.name}.tmp")
        temporary.write_bytes(content.encode("utf-8"))
        os.replace(temporary, target)

    def _list_file_paths(self, prefix: str = "") -> list[str]:
        resolved = self._resolve(prefix)
        if resolved.is_file():
            return [self._relative(resolved)]
        if not resolved.exists() or not resolved.is_dir():
            return []
        paths: list[str] = []
        for current_root, directories, filenames in os.walk(resolved):
            directories[:] = sorted(
                name for name in directories
                if not ignored_directory(name) and not name.startswith(".git")
            )
            for filename in filenames:
                paths.append(self._relative(Path(current_root) / filename))
        return sorted(paths)

    def _current_version(self, path: str) -> str | None:
        resolved = self._resolve(path)
        try:
            info = resolved.stat()
        except FileNotFoundError:
            return None
        return f"{info.st_mtime_ns}:{info.st_size}"

    # ---- 目录与 stat ----
    def list_entries(self, prefix: str = "") -> list[FileEntry]:
        resolved = self._resolve(prefix)
        entries: list[FileEntry] = []
        if resolved.is_file():
            relative = self._relative(resolved)
            info = resolved.stat()
            entries.append(FileEntry(
                path=relative, type="file", mode=self.mode(relative), size_bytes=info.st_size
            ))
            return entries
        if not resolved.exists() or not resolved.is_dir():
            return []
        for current_root, directories, filenames in os.walk(resolved):
            directories[:] = sorted(
                name for name in directories
                if not ignored_directory(name) and not name.startswith(".git")
            )
            for directory in directories:
                relative_dir = self._relative(Path(current_root) / directory)
                entries.append(FileEntry(path=relative_dir, type="directory", mode="dir"))
            for filename in filenames:
                absolute = Path(current_root) / filename
                relative = self._relative(absolute)
                entries.append(FileEntry(
                    path=relative,
                    type="file",
                    mode=self.mode(relative),
                    size_bytes=absolute.stat().st_size,
                ))
        return sorted(entries, key=lambda entry: entry.path)

    def _file_stat(self, path: str) -> FileStat:
        resolved = self._resolve(path)
        if not resolved.exists():
            raise KeyError(path)
        info = resolved.stat()
        if resolved.is_dir():
            return FileStat(path=normalize_path(path), type="directory")
        line_count = None
        if info.st_size <= self.config.max_read_bytes:
            try:
                lines, _ = line_info(self._read_file(path))
                line_count = len(lines)
            except WorkspaceError:
                line_count = None
        return FileStat(
            path=normalize_path(path),
            type="file",
            size_bytes=info.st_size,
            line_count=line_count,
            version=f"{info.st_mtime_ns}:{info.st_size}",
        )
