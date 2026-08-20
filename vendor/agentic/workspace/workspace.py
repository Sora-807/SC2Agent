"""Workspace 抽象基类:文件契约的读写接口 + read-before-write 观察策略。

MemoryWorkspace / DiskWorkspace 继承本类,实现底层存储与文件发现;
ScopedWorkspace 是多 agent 的权限视图(见 scoped.py),与 Workspace 保持相同方法签名。

观察策略(默认开启):
- read_text 成功后记录该文件的版本。
- 对已存在文件的 write / append / edit / insert / delete,要求该文件在本次会话中已被
  read 过,且版本未变,否则拒绝并提示重新 read。
- 新建文件不受限制;任何写操作成功后都会记录新版本,因此刚写过的文件可继续修改,
  不需要重新 read。
"""
from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


class WorkspaceError(Exception):
    """Workspace 层所有可预期错误的基类;工具层会把它转成 error: 字符串回喂模型。"""


class FileNotObservedError(WorkspaceError):
    """文件存在但本次会话尚未 read 过,拒绝修改。"""


class FileChangedError(WorkspaceError):
    """文件自上次 read 后已变化,拒绝修改。"""


class InvalidPathError(WorkspaceError):
    """路径越界、为绝对路径、或包含 .. 等非法片段。"""


@dataclass
class FileEntry:
    """list 工具展示用的目录项。"""
    path: str
    type: str  # "file" | "directory"
    mode: str = "rw"  # "ro" | "rw";目录固定为 "dir"
    size_bytes: int | None = None
    line_count: int | None = None


@dataclass
class FileStat:
    """stat 工具返回的文件元信息。"""
    path: str
    type: str  # "file" | "directory"
    size_bytes: int | None = None
    line_count: int | None = None
    version: str | None = None


@dataclass
class GrepMatch:
    """grep 命中的一行。"""
    path: str
    line_number: int
    line: str


@dataclass
class WorkspaceConfig:
    """Workspace 通用配置。"""
    require_read_before_write: bool = True
    max_read_bytes: int = 10 * 1024 * 1024  # 单文件最大读取字节数,防止误读超大文件


_IGNORED_DIRECTORIES = {".git", ".svn", ".hg", ".bzr", ".jj", ".sl", "node_modules", "__pycache__"}


def normalize_path(path: str) -> str:
    """把用户给的路径规范成工作区相对路径(用 / 分隔)。"""
    path = (path or "").replace("\\", "/")
    path = path.strip("/")
    parts = [part for part in path.split("/") if part not in ("", ".")]
    if any(part == ".." for part in parts):
        raise InvalidPathError(f"非法路径 '{path}':路径中不允许包含 '..'")
    return "/".join(parts)


def is_absolute_path(path: str) -> bool:
    """判断用户路径是否是绝对路径(Windows 盘符或 POSIX /)。"""
    if path is None:
        return False
    text = str(path).replace("\\", "/")
    return text.startswith("/") or bool(re.match(r"^[A-Za-z]:/", text))


def line_info(text: str) -> tuple[list[str], str]:
    """把文本拆成行,并返回换行符风格。

    尾随换行符不计入最后一行(与 read 工具的显示行数保持一致)。
    """
    if text == "":
        return [], "\n"
    newline = "\r\n" if "\r\n" in text else "\n"
    parts = text.split("\n")
    if parts and parts[-1] == "":
        parts.pop()
    return [part[:-1] if part.endswith("\r") else part for part in parts], newline


def join_lines(lines: list[str], newline: str = "\n") -> str:
    """把行列表按指定换行符拼回文本;空列表返回空字符串。"""
    if not lines:
        return ""
    return newline.join(lines)


def ignored_directory(name: str) -> bool:
    """列目录、glob、grep 时跳过的目录名(VCS 元数据与常见缓存目录)。"""
    return name in _IGNORED_DIRECTORIES


class Workspace(ABC):
    """文件契约工作区基类。

    子类实现 4 个存储原语(_file_exists / _read_file / _write_file / _list_file_paths),
    line 级编辑、glob、grep、list 等通用逻辑在本类完成。
    """

    def __init__(self, config: WorkspaceConfig | None = None) -> None:
        self.config = config or WorkspaceConfig()
        self._observed_versions: dict[str, Any] = {}

    # ---- 子类存储原语 ----
    @abstractmethod
    def _file_exists(self, path: str) -> bool: ...

    @abstractmethod
    def _read_file(self, path: str) -> str: ...

    @abstractmethod
    def _write_file(self, path: str, content: str) -> None: ...

    @abstractmethod
    def _list_file_paths(self, prefix: str = "") -> list[str]: ...

    @abstractmethod
    def _current_version(self, path: str) -> str | None: ...

    # ---- 观察策略 ----
    def _require_observed(self, path: str) -> None:
        if not self.config.require_read_before_write:
            return
        if not self._file_exists(path):
            raise KeyError(path)
        observed = self._observed_versions.get(path)
        if observed is None:
            raise FileNotObservedError(f"{path} 存在但尚未 read 过;请先 read 再修改")
        current = self._current_version(path)
        if observed != current:
            raise FileChangedError(f"{path} 自上次 read 后已变化;请重新 read 再修改")

    def _note_observed(self, path: str) -> None:
        version = self._current_version(path)
        if version is not None:
            self._observed_versions[path] = version

    # ---- 读 ----
    def read_text(self, path: str) -> str:
        """读文件全文,成功后记录观察版本。"""
        text = self._read_file(path)
        self._note_observed(path)
        return text

    def read(self, path: str) -> str:
        """read_text 的简短别名,供钩子/调用方使用。"""
        return self.read_text(path)

    def contains(self, path: str) -> bool:
        return self._file_exists(path)

    def __contains__(self, path: str) -> bool:
        return self.contains(path)

    # ---- 写 ----
    def add_document(self, path: str, content: str) -> None:
        """注册/刷新一份文档(供 Engine 初始化文档使用),不记录观察状态。

        文档注册后,agent 仍需先 read 才能覆盖/编辑它;新建文件则不受限制。
        """
        self._write_file(path, content)

    def write_text(self, path: str, content: str) -> None:
        """整文件创建或覆盖;覆盖已存在文件前要求先 read。"""
        exists = self._file_exists(path)
        if exists:
            self._require_observed(path)
        self._write_file(path, content)
        self._note_observed(path)

    def append_text(self, path: str, content: str) -> None:
        """在文件末尾追加内容;已存在文件要求先 read。"""
        if self._file_exists(path):
            self._require_observed(path)
            old = self._read_file(path)
            self._write_file(path, old + content)
        else:
            self._write_file(path, content)
        self._note_observed(path)

    def edit_text(self, path: str, old_string: str, new_string: str, replace_all: bool = False) -> int:
        """字面量替换:old_string 必须精确存在,默认要求唯一;replace_all=True 时替换所有。"""
        if old_string == "":
            raise WorkspaceError("old_string 不能为空")
        if old_string == new_string:
            raise WorkspaceError("old_string 与 new_string 不能相同")
        self._require_observed(path)
        before = self._read_file(path)
        count = before.count(old_string)
        if count == 0:
            raise WorkspaceError(f"old_string 在 {path} 中未找到")
        if count > 1 and not replace_all:
            raise WorkspaceError(
                f"old_string 在 {path} 中出现了 {count} 次;请提供更具体的 old_string 或设置 replace_all=true"
            )
        after = before.replace(old_string, new_string) if replace_all else before.replace(old_string, new_string, 1)
        self._write_file(path, after)
        self._note_observed(path)
        return count

    def insert_text(self, path: str, line_number: int, content: str) -> None:
        """在第 line_number 行之后插入内容;line_number=0 表示文件开头。"""
        self._require_observed(path)
        before = self._read_file(path)
        lines, newline = line_info(before)
        if line_number < 0 or line_number > len(lines):
            raise WorkspaceError(f"line_number {line_number} 超出范围 [0, {len(lines)}]")
        inserted, _ = line_info(content)
        new_lines = lines[:line_number] + inserted + lines[line_number:]
        self._write_file(path, join_lines(new_lines, newline))
        self._note_observed(path)

    def delete_lines(self, path: str, start_line: int, end_line: int) -> int:
        """删除 [start_line, end_line] 闭区间内的行(1-based),返回删除行数。"""
        self._require_observed(path)
        before = self._read_file(path)
        lines, newline = line_info(before)
        if start_line < 1 or end_line > len(lines) or start_line > end_line:
            raise WorkspaceError(f"行区间 [{start_line}, {end_line}] 超出范围 [1, {len(lines)}]")
        new_lines = lines[: start_line - 1] + lines[end_line:]
        self._write_file(path, join_lines(new_lines, newline))
        self._note_observed(path)
        return end_line - start_line + 1

    # ---- 发现 ----
    def list_entries(self, prefix: str = "") -> list[FileEntry]:
        """返回 prefix 下的文件与目录(目录从文件路径推导,磁盘实现可覆盖)。"""
        files = [self._file_stat(path) for path in self._list_file_paths(prefix)]
        entries: list[FileEntry] = []
        directories: set[str] = set()
        for stat in files:
            entries.append(FileEntry(
                path=stat.path,
                type="file",
                mode=self.mode(stat.path),
                size_bytes=stat.size_bytes,
                line_count=stat.line_count,
            ))
            parts = stat.path.split("/")
            for i in range(1, len(parts)):
                directories.add("/".join(parts[:i]))
        for directory in sorted(directories):
            entries.append(FileEntry(path=directory, type="directory", mode="dir"))
        return sorted(entries, key=lambda entry: entry.path)

    def stat(self, path: str) -> FileStat:
        return self._file_stat(path)

    def mode(self, path: str) -> str:
        return "rw"

    def glob(self, pattern: str, path: str = "") -> list[str]:
        """按 glob pattern 查找文件;pattern 不含 / 时匹配 basename,含 / 时锚定目录。"""
        matcher = _glob_matcher(pattern)
        return [p for p in self._list_file_paths(path) if matcher(p)]

    def grep(self, pattern: str, path: str = "", include: str | None = None) -> list[GrepMatch]:
        """正则搜索文件内容,按文件路径与 include glob 过滤。"""
        regex = re.compile(pattern)
        include_matcher = _glob_matcher(include) if include else None
        matches: list[GrepMatch] = []
        for file_path in self._list_file_paths(path):
            if include_matcher and not include_matcher(file_path):
                continue
            try:
                lines, _ = line_info(self._read_file(file_path))
            except WorkspaceError:
                continue
            for number, text in enumerate(lines, 1):
                if regex.search(text):
                    matches.append(GrepMatch(file_path, number, text))
        return matches

    def _file_stat(self, path: str) -> FileStat:
        """默认实现:文件大小与行数按需读取;子类可覆盖以提供更廉价的信息。"""
        text = self._read_file(path)
        lines, newline = line_info(text)
        return FileStat(
            path=path,
            type="file",
            size_bytes=len(text.encode("utf-8")),
            line_count=len(lines) if text else 0,
            version=self._current_version(path),
        )

    # ---- 产出 ----
    def snapshot(self) -> dict[str, str]:
        """返回 {path: content},供持久化使用。"""
        result: dict[str, str] = {}
        for path in self._list_file_paths():
            try:
                result[path] = self._read_file(path)
            except WorkspaceError:
                continue
        return result

    def workspace_out(self) -> dict[str, str]:
        """runner 收尾时返回本次产出(裸工作区返回全部文件)。"""
        return self.snapshot()

    def visible_paths(self) -> list[str]:
        return self._list_file_paths()


def _glob_matcher(pattern: str):
    """把 glob pattern 编译成匹配函数。"""
    normalized = pattern.replace("\\", "/")
    if "/" not in normalized:
        segment = re.compile(_segment_regex(normalized))
        return lambda path: bool(segment.match(path.rsplit("/", 1)[-1]))
    regex = re.compile("^" + "/".join(_segment_regex(part) for part in normalized.split("/")) + "$")
    return lambda path: bool(regex.match(path))


def _segment_regex(segment: str) -> str:
    """单个 glob 段的转义:*.ts 等;不实现完整 glob 语法,但覆盖常用通配符。"""
    out: list[str] = []
    index = 0
    while index < len(segment):
        char = segment[index]
        if char == "*":
            out.append("[^/]*")
        elif char == "?":
            out.append("[^/]")
        elif char == "[":
            end = segment.find("]", index + 1)
            if end == -1:
                out.append(r"\[")
            else:
                content = segment[index + 1:end]
                if content.startswith("!"):
                    content = "^" + content[1:]
                out.append(f"[{content}]")
                index = end
        else:
            out.append(re.escape(char))
        index += 1
    return "".join(out)
