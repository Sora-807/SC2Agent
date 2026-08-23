"""agent.readonly：运行时产物挂成 Agent 文件树的**只读区**（I20 文件契约闭环）。

写面（plans/map-plans/strategies）早已用"REST 虚拟成路径 + 保存即校验"闭环；这里把
同一原则扩到读面：历史产物（录像/轨迹/提案日志）挂成虚拟路径，Agent 用现有的
ls/read/grep 翻，**不新增 bespoke 工具** —— 每个产物一套工具正是孤儿反模式的延续
（notes.jsonl 一次、recordings 一次，不能有第三次）。

只读区是**磁盘直读**而不是走 REST：这些文件不可变，没有"绕过校验"的写面风险
（"与 UI 同一入口"的红线约束的是写），而 traces 根本没有 REST 面。

大文件保护：录像原始帧流（几 MB jsonl）刻意不挂 —— read/grep 会把它整份吃进
上下文。挂的是衍生摘要（`view.recap` 渲染的 `rec-<id>.md`，缺失时懒生成并落盘）
与清单索引；`.meta.json` / `.jsonl` 同理拒绝并指路。
"""
from __future__ import annotations

import json
from pathlib import Path

from agentic.workspace.workspace import WorkspaceError

from view.recap import render_recording_summary, render_recordings_index

#: traces 区暴露的文件后缀白名单：trace.md / summary.json / run.meta.json / tree.json
#: 是金子；trace.html（大且是给人看的可视化）与快照目录不进白名单之外的口子。
_TEXT_SUFFIXES = (".md", ".json")


class ReadOnlyArea:
    """只读虚拟区基类：full 虚拟路径进、full 虚拟路径出，由 ApiWorkspace 统一挂载。"""

    prefix: str = ""

    def handles(self, path: str) -> bool:
        return path == self.prefix or path.startswith(self.prefix.rstrip("/") + "/")

    def list_paths(self, prefix: str = "") -> list[str]:
        raise NotImplementedError

    def exists(self, path: str) -> bool:
        try:
            self.read(path)
            return True
        except WorkspaceError:
            return False

    def read(self, path: str) -> str:
        raise NotImplementedError


class RecordingsArea(ReadOnlyArea):
    """`recordings/`：index.md（清单索引）+ 每局 rec-<id>.md（衍生摘要，懒生成）。"""

    prefix = "recordings/"

    def __init__(self, root: Path) -> None:
        self._root = Path(root)

    def _metas(self) -> list[dict]:
        out: list[dict] = []
        if self._root.is_dir():
            for meta_path in sorted(self._root.glob("rec-*.meta.json"), reverse=True):
                try:
                    meta = json.loads(meta_path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue
                meta.setdefault("id", meta_path.name.replace(".meta.json", ""))
                out.append(meta)
        return out

    def list_paths(self, prefix: str = "") -> list[str]:
        paths = ["recordings/index.md"] + [f"recordings/{m['id']}.md" for m in self._metas()]
        return [p for p in paths if p.startswith(prefix)]

    def read(self, path: str) -> str:
        if path == "recordings/index.md":
            return render_recordings_index(self._metas())
        if not path.startswith("recordings/") or not path.endswith(".md"):
            raise WorkspaceError(
                f"{path!r} 不在只读区（recordings/ 只挂 index.md 与每局摘要 .md；"
                "原始帧流 .jsonl 刻意不挂 —— 几 MB 会撑爆上下文，用摘要）")
        rid = path[len("recordings/"):-len(".md")]
        md = self._root / f"{rid}.md"
        if md.is_file():
            return md.read_text(encoding="utf-8")
        jsonl = self._root / f"{rid}.jsonl"
        if not jsonl.is_file():
            raise WorkspaceError(f"没有对局记录 {rid!r}（read recordings/index.md 看有哪些）")
        # 懒生成：被 kill -9 的会话没走到收尾 —— 从原始帧流补一份摘要并落盘
        rows = [json.loads(line) for line in jsonl.read_text(encoding="utf-8").splitlines()
                if line.strip()]
        text = render_recording_summary(rows)
        try:
            md.write_text(text, encoding="utf-8")
        except OSError:
            pass    # 落不了盘（只读目录之类）就每次现算，读功能不受影响
        return text


class TraceArea(ReadOnlyArea):
    """`traces/`：会话执行轨迹（trace.md / summary.json / run.meta.json / tree.json）。"""

    prefix = "traces/"

    def __init__(self, root: Path) -> None:
        self._root = Path(root)

    def list_paths(self, prefix: str = "") -> list[str]:
        if not self._root.is_dir():
            return []
        out = [f"traces/{p.relative_to(self._root).as_posix()}"
               for p in sorted(self._root.rglob("*"))
               if p.is_file() and p.suffix in _TEXT_SUFFIXES]
        return [p for p in out if p.startswith(prefix)]

    def read(self, path: str) -> str:
        rel = path[len("traces/"):]
        target = (self._root / rel).resolve()
        root = self._root.resolve()
        if not str(target).startswith(str(root)):
            raise WorkspaceError(f"{path!r} 越出 traces 只读区")
        if not target.is_file() or target.suffix not in _TEXT_SUFFIXES:
            raise WorkspaceError(
                f"{path!r} 不在 traces 白名单（只挂 {'/'.join(_TEXT_SUFFIXES)}；"
                "trace.html 是给人看的可视化，不进上下文）")
        return target.read_text(encoding="utf-8")


class SingleFileArea(ReadOnlyArea):
    """单个只读文件（如 `proposals/log.jsonl` ← runtime/proposals.jsonl 提案审计史）。"""

    def __init__(self, vpath: str, path: Path) -> None:
        self.prefix = vpath
        self._path = Path(path)

    def list_paths(self, prefix: str = "") -> list[str]:
        return [self.prefix] if self._path.is_file() and self.prefix.startswith(prefix) else []

    def read(self, path: str) -> str:
        if path != self.prefix or not self._path.is_file():
            raise WorkspaceError(f"没有 {self.prefix!r}（提案历史还没写过）")
        return self._path.read_text(encoding="utf-8")


def default_areas(*, trace_root: Path, recordings_dir: Path | None,
                  proposals_log: Path | None) -> list[ReadOnlyArea]:
    """默认只读区装配（AgentTalk / 测试共用；目录不存在 = 空清单，不炸）。"""
    areas: list[ReadOnlyArea] = [TraceArea(trace_root)]
    if recordings_dir is not None:
        areas.append(RecordingsArea(recordings_dir))
    if proposals_log is not None:
        areas.append(SingleFileArea("proposals/log.jsonl", proposals_log))
    return areas
