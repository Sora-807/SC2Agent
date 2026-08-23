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
import re
from pathlib import Path

import yaml
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


class MapsArea(ReadOnlyArea):
    """`maps/`（I18，2026-08-23 用户拍板文件形态）：**看地图 = 引用一个文件**。

    - `maps/index.md` —— 源清单（live + 各地图规划）+ 路径约定 + 格点词表
    - `maps/<源>/<x1>_<y1>_<x2>_<y2>.md` —— 框选格点网格（Markdown 表格；
      可加 `_s<k>` 后缀指定步长，如 `..._s2.md`）。按需现算，不落盘。

    源 = `live`（当前会话装配的地图规划；无会话时退出厂 bl 布局并注明）或
    任意地图规划 id（runtime/map-plans/<id>.yaml）。栅格只看**布局结构**
    （槽位/预设点/地形），建造状态归 observe —— 用户拍板不进网格。
    """

    prefix = "maps/"
    _REGION_PATH = re.compile(
        r"maps/(?P<src>[\w.-]+)/(?P<x1>-?\d+)_(?P<y1>-?\d+)_(?P<x2>-?\d+)_(?P<y2>-?\d+)(?:_s(?P<step>\d+))?\.md")

    def __init__(self, client, map_plans_dir: Path) -> None:
        self._client = client
        self._root = Path(map_plans_dir)
        self._catalog = None      # reserved_boxes 要目录；首用懒加载（构造别拖 catalog）

    def _plans(self) -> list[dict]:
        out = []
        if self._root.is_dir():
            for p in sorted(self._root.glob("*.yaml")):
                try:
                    d = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
                except (OSError, yaml.YAMLError):
                    continue
                out.append({"id": d.get("id") or p.stem,
                            "title_zh": d.get("title_zh") or "",
                            "spawn": d.get("spawn") or "bl",
                            "slots": len(d.get("build_slots") or {})})
        return out

    def list_paths(self, prefix: str = "") -> list[str]:
        return ["maps/index.md"] if "maps/index.md".startswith(prefix) else []

    def _resolve_source(self, src: str) -> tuple[Path, str]:
        """源 → (规划文件, 说明)。live 问当前会话；其余必须是磁盘上的规划 id。"""
        if src == "live":
            plan_id = None
            try:
                info = self._client.session()
                plan_id = (info or {}).get("map_plan_id") or (
                    Path(str((info or {}).get("map_plan_path") or "")).stem or None)
            except Exception:      # noqa: BLE001 —— 后端没答（未启用/离线）就退出厂
                pass
            if plan_id and (self._root / f"{plan_id}.yaml").is_file():
                return self._root / f"{plan_id}.yaml", f"live → 会话规划 {plan_id}"
            return self._root / "layout-bl.yaml", \
                "live 无会话或未指名地图规划 → 出厂 bl 布局（tr 侧用 maps/layout-tr/）"
        path = self._root / f"{src}.yaml"
        if not path.is_file():
            raise WorkspaceError(
                f"没有地图源 {src!r}（read maps/index.md 看有哪些：live + 各地图规划 id）")
        return path, ""

    def read(self, path: str) -> str:
        if path == "maps/index.md":
            return self._index()
        m = self._REGION_PATH.fullmatch(path)
        if m is None:
            raise WorkspaceError(
                f"{path!r} 不是合法的区域路径 —— 约定 maps/<源>/<x1>_<y1>_<x2>_<y2>.md"
                "（bbox=左下+右上闭区间；步长后缀 _s2；源与示例见 maps/index.md）")
        plan_path, note = self._resolve_source(m["src"])
        try:
            doc = yaml.safe_load(plan_path.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError) as exc:
            raise WorkspaceError(f"地图规划 {m['src']!r} 读不了：{exc}") from None
        from tactical_map.region_view import render_region

        if self._catalog is None:
            from game.catalog import load_all

            self._catalog = load_all()
        try:
            text = render_region(
                (int(m["x1"]), int(m["y1"]), int(m["x2"]), int(m["y2"])),
                doc.get("build_slots") or {}, self._catalog,
                step=int(m["step"] or 1), title=m["src"])
        except ValueError as exc:
            raise WorkspaceError(str(exc)) from None
        if note:
            text = f"> {note}\n\n" + text
        return text

    def _index(self) -> str:
        from tactical_map.region_view import LEGEND

        rows = self._plans()
        out = ["# 地图源清单（看地图 = 引用一个文件）", "",
               "读 `maps/<源>/<x1>_<y1>_<x2>_<y2>.md` 拿格点网格（bbox = 左下+右上，闭区间；"
               "步长后缀 `_s2` 降密度；全图 176×160）。**网格只看布局结构** —— 建造状态去 "
               "observe。", "",
               LEGEND, "", "## 源", "",
               "| 源 | 说明 | 槽位数 |", "|---|---|---|",
               "| `live` | 当前会话装配的地图规划（无会话 = 出厂 bl 布局） | — |"]
        out += [f"| `{r['id']}` | {r['title_zh']}（spawn {r['spawn']}） | {r['slots']} |"
                for r in rows]
        out += ["", "示例：`read maps/live/38_27_52_41_s2.md` —— 主矿补给站方阵 + 工厂区。",
                "`default-*` 是空白预设（无槽位，只有地形/预设点）；出厂校准布局在 `layout-bl/tr`。"]
        return "\n".join(out) + "\n"


def default_areas(*, client, trace_root: Path, recordings_dir: Path | None,
                  proposals_log: Path | None,
                  map_plans_dir: Path | None = None) -> list[ReadOnlyArea]:
    """默认只读区装配（AgentTalk / 测试共用；目录不存在 = 空清单，不炸）。

    `client` 供 maps/ 的 live 源解析（问当前会话装配的规划）。
    `map_plans_dir=None` 时 maps/ 区不挂（测试隔离用）。
    """
    areas: list[ReadOnlyArea] = [TraceArea(trace_root)]
    if recordings_dir is not None:
        areas.append(RecordingsArea(recordings_dir))
    if proposals_log is not None:
        areas.append(SingleFileArea("proposals/log.jsonl", proposals_log))
    if map_plans_dir is not None:
        areas.append(MapsArea(client, map_plans_dir))
    return areas
