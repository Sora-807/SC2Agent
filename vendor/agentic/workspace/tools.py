"""workspace 工具:把 Workspace 包装成 LLM 可调用的 Tool。

工具语义参考 DSH 文件工具:
- read 返回行号文本,不返回 hash;行编辑用字面量或行号。
- edit 用 old_string/new_string/replace_all,默认要求唯一匹配。
- 所有错误统一以 error: 开头返回给模型,模型可按提示自行纠正。
"""
from __future__ import annotations

import re

from ..types import Tool
from .workspace import Workspace, line_info

READ_DEFAULT_LIMIT = 2000
READ_MAX_LINE_LENGTH = 2000
GLOB_MAX_RESULTS = 100
GREP_MAX_MATCHES = 250


def _error_message(error: Exception) -> str:
    return f"error: {error}"


def _safe(function):
    """把同步 workspace 调用包成 async,并把异常转成 error: 字符串。"""
    async def wrapped(arguments: dict) -> str:
        try:
            result = function(arguments)
            return str(result)
        except Exception as error:  # noqa: BLE001
            return _error_message(error)
    return wrapped


def _line_count(content: str) -> int:
    lines, _ = line_info(content)
    return len(lines)


def _render_entries(entries) -> str:
    """把 FileEntry 列表渲染成目录树。"""
    if not entries:
        return "(empty)"
    root: dict = {}
    for entry in entries:
        parts = entry.path.split("/")
        node = root
        for part in parts[:-1]:
            child = node.get(part)
            if not isinstance(child, dict):
                child = {}
                node[part] = child
            node = child
        if entry.type == "directory":
            if not isinstance(node.get(parts[-1]), dict):
                node[parts[-1]] = {}
        else:
            node[parts[-1]] = entry

    lines: list[str] = []

    def emit(node: dict, prefix: str) -> None:
        for name in sorted(node.keys()):
            value = node[name]
            if isinstance(value, dict):
                lines.append(f"{prefix}{name}/")
                emit(value, prefix + "  ")
            else:
                detail = value.mode
                if value.line_count is not None:
                    detail += f", {value.line_count} lines"
                elif value.size_bytes is not None:
                    detail += f", {value.size_bytes} bytes"
                lines.append(f"{prefix}{name} ({detail})")

    emit(root, "")
    return "\n".join(lines)


def make_workspace_tools(workspace: Workspace) -> list[Tool]:
    """返回文件契约基础工具列表(ls/read/glob/grep/write/append/edit/insert/delete/stat)。"""

    def list_files(arguments: dict) -> str:
        prefix = arguments.get("path", "")
        entries = workspace.list_entries(prefix)
        return _render_entries(entries)

    def read_file(arguments: dict) -> str:
        path = arguments["path"]
        if not workspace.contains(path):
            return f"error: {path} not found"
        offset = int(arguments.get("offset", 1))
        limit = int(arguments.get("limit", READ_DEFAULT_LIMIT))
        if offset < 1:
            return "error: offset must be a positive integer"
        if limit < 1:
            return "error: limit must be a positive integer"
        text = workspace.read_text(path)
        lines, _ = line_info(text)
        total = len(lines)
        if total == 0:
            return f"[{path} 为空(0 行)]"
        if offset > total:
            return f"error: offset {offset} 超出范围 [1, {total}]"
        end = min(offset + limit - 1, total)
        shown = lines[offset - 1:end]
        body = "\n".join(
            f"{number}: {text[:READ_MAX_LINE_LENGTH]}{'...(line truncated)' if len(text) > READ_MAX_LINE_LENGTH else ''}"
            for number, text in enumerate(shown, start=offset)
        )
        if end < total:
            footer = f"\n(显示第 {offset}-{end} 行 / 共 {total} 行;offset={end + 1} 继续读取)"
        else:
            footer = f"\n(显示第 {offset}-{end} 行 / 共 {total} 行)"
        return body + footer

    def glob_files(arguments: dict) -> str:
        pattern = arguments["pattern"]
        path = arguments.get("path", "")
        matches = workspace.glob(pattern, path)
        if not matches:
            return "No files found"
        total = len(matches)
        shown = matches[:GLOB_MAX_RESULTS]
        body = "\n".join(shown)
        if total > GLOB_MAX_RESULTS:
            body += f"\n(共 {total} 个文件,只显示前 {GLOB_MAX_RESULTS} 个;请缩小 pattern 或 path)"
        return body

    def grep_files(arguments: dict) -> str:
        pattern = arguments["pattern"]
        path = arguments.get("path", "")
        include = arguments.get("include")
        try:
            re.compile(pattern)
        except re.error as error:
            return f"error: 无效正则: {error}"
        matches = workspace.grep(pattern, path, include)
        if not matches:
            return "No matches found"
        total = len(matches)
        shown = matches[:GREP_MAX_MATCHES]
        grouped: dict[str, list] = {}
        for match in shown:
            grouped.setdefault(match.path, []).append(match)
        sections = [
            f"{file_path}\n" + "\n".join(f"Line {match.line_number}: {match.line}" for match in group)
            for file_path, group in grouped.items()
        ]
        body = "\n\n".join(sections)
        if total > GREP_MAX_MATCHES:
            body += f"\n(共 {total} 处命中,只显示前 {GREP_MAX_MATCHES} 处;请缩小 pattern/path/include)"
        return body

    def write_file(arguments: dict) -> str:
        path, content = arguments["path"], arguments["content"]
        workspace.write_text(path, content)
        return f"wrote {path} ({_line_count(content)} lines)"

    def append_file(arguments: dict) -> str:
        path, content = arguments["path"], arguments["content"]
        workspace.append_text(path, content)
        return f"appended {_line_count(content)} lines to {path}"

    def edit_file(arguments: dict) -> str:
        path = arguments["path"]
        old_string = arguments["old_string"]
        new_string = arguments["new_string"]
        replace_all = bool(arguments.get("replace_all", False))
        count = workspace.edit_text(path, old_string, new_string, replace_all)
        return f"edited {path} ({count} replacement(s))"

    def insert_file(arguments: dict) -> str:
        path = arguments["path"]
        line_number = int(arguments["line_number"])
        content = arguments["content"]
        workspace.insert_text(path, line_number, content)
        return f"inserted {_line_count(content)} lines into {path} after line {line_number}"

    def delete_lines(arguments: dict) -> str:
        path = arguments["path"]
        start_line = int(arguments["start_line"])
        end_line = int(arguments["end_line"])
        count = workspace.delete_lines(path, start_line, end_line)
        return f"deleted {count} lines from {path}"

    def stat_file(arguments: dict) -> str:
        path = arguments["path"]
        stat = workspace.stat(path)
        detail = f"path: {stat.path}\ntype: {stat.type}"
        if stat.size_bytes is not None:
            detail += f"\nsize_bytes: {stat.size_bytes}"
        if stat.line_count is not None:
            detail += f"\nline_count: {stat.line_count}"
        return detail

    return [
        Tool(
            name="ls",
            description="List files and directories under a path (default: workspace root). Shows mode ro/rw and line count or size.",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "optional path prefix, e.g. 'inputs/'"},
                },
            },
            function=_safe(list_files),
        ),
        Tool(
            name="read",
            description=(
                "Read a UTF-8 text file with line numbers. Use offset/limit to page through large files. "
                "Returns lines as '{lineno}: {text}' with a footer telling you how to continue."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "offset": {"type": "integer", "description": "1-based first line to return (default 1)"},
                    "limit": {"type": "integer", "description": "max lines to return (default 2000)"},
                },
                "required": ["path"],
            },
            function=_safe(read_file),
        ),
        Tool(
            name="glob",
            description=(
                "Find files whose paths match a glob pattern. Pattern without '/' matches basename at any depth; "
                "include a separator to anchor depth. Returns file paths only."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "glob pattern, e.g. '**/*.ts' or '*.md'"},
                    "path": {"type": "string", "description": "directory to search (default workspace root)"},
                },
                "required": ["pattern"],
            },
            function=_safe(glob_files),
        ),
        Tool(
            name="grep",
            description=(
                "Search file contents with a regular expression. Returns matching lines grouped by file. "
                "Use read on a matched file for surrounding context."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "regular expression to search for"},
                    "path": {"type": "string", "description": "file or directory to search (default workspace root)"},
                    "include": {"type": "string", "description": "one glob filter, e.g. '*.ts' or '*.{js,jsx}'"},
                },
                "required": ["pattern"],
            },
            function=_safe(grep_files),
        ),
        Tool(
            name="write",
            description="Create or fully replace a UTF-8 text file. Existing files must be read first.",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string", "description": "full UTF-8 text content to write"},
                },
                "required": ["path", "content"],
            },
            function=_safe(write_file),
        ),
        Tool(
            name="append",
            description="Append text to the end of a UTF-8 text file. Existing files must be read first.",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string", "description": "text to append"},
                },
                "required": ["path", "content"],
            },
            function=_safe(append_file),
        ),
        Tool(
            name="edit",
            description=(
                "Edit an existing UTF-8 text file by replacing literal text. old_string must appear exactly once "
                "unless replace_all=true; use an empty new_string to delete the match."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "old_string": {"type": "string", "description": "literal text to replace; must match exactly"},
                    "new_string": {"type": "string", "description": "literal replacement text; use '' to delete the match"},
                    "replace_all": {"type": "boolean", "description": "replace all matches (default false; when false old_string must be unique)"},
                },
                "required": ["path", "old_string", "new_string"],
            },
            function=_safe(edit_file),
        ),
        Tool(
            name="insert",
            description=(
                "Insert text after a line number. line_number=0 inserts at the head of the file; "
                "text may be multi-line."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "line_number": {"type": "integer", "description": "insert after this line (0 = file head)"},
                    "content": {"type": "string", "description": "text to insert, may be multi-line"},
                },
                "required": ["path", "line_number", "content"],
            },
            function=_safe(insert_file),
        ),
        Tool(
            name="delete",
            description="Delete lines start_line..end_line (inclusive, 1-based) from a UTF-8 text file.",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "start_line": {"type": "integer", "description": "first line to delete (1-based)"},
                    "end_line": {"type": "integer", "description": "last line to delete (inclusive)"},
                },
                "required": ["path", "start_line", "end_line"],
            },
            function=_safe(delete_lines),
        ),
        Tool(
            name="stat",
            description="Show file metadata: type, size in bytes, and line count.",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                },
                "required": ["path"],
            },
            function=_safe(stat_file),
        ),
    ]
