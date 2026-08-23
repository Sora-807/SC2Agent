"""agent.workspace：把规划域包成一个**虚拟文件工作区**（2026-08-22 拍板方向）。

agent 对规划的操作从 12 个 CRUD 工具收敛为框架的文件契约
（ls/read/glob/grep/write/edit/insert/delete/stat，见 vendor/agentic），
本类是这些工具背后的存储：三类虚拟路径区，按路径区分语义与校验 ——

- ``plans/<id>.yaml``     生产规划 → REST 规划 API（GET 读 / PUT 全量保存，校验在服务端）
- ``map-plans/<id>.yaml`` 地图规划 → 地图规划 API（doc 读 / 全量 PUT，几何校验在服务端）
- ``strategies/<id>.yaml`` 策略 → 策略 API（strategy+assembly 两段；二十七轮用户拍板
                          「开放写策略，免审」，保存时 parse/validate 全套编译期校验）
- ``recordings/`` ``traces/`` ``proposals/log.jsonl``
                          只读区（I20）：运行时产物挂进文件树，write 一律拒绝 ——
                          历史不可变；适配器在 ``agent/readonly.py``
- 其余路径               scratch 自留地（memory/、analysis.md…）：磁盘直写，不校验

所有规划写都走与 UI 相同的 REST 入口（决策 U7，无后门），校验失败以
``WorkspaceError`` 回喂模型（文件工具层转成 error: 前缀）。读到的 YAML 由本类
从 JSON 渲染（yaml.safe_dump，与服务端同参数）—— agent 看到和改动的始终是
同一份渲染，edit/insert 的文本操作在解析后整体 PUT，天然保持校验闭环。

**ChangeRecord**：写钩子在保存成功后记一条改动（live 域的提案自动应用由
propose 工具记录）。AgentTalk 每轮结束 drain，进对话历史 → 前端渲染跳转 chip。
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

import yaml
from agentic.workspace.disk import DiskWorkspace
from agentic.workspace.workspace import Workspace, WorkspaceConfig, WorkspaceError

from agent.client import ApiClient, ApiError
from agent.readonly import ReadOnlyArea

PLAN_PREFIX = "plans/"
MAP_PREFIX = "map-plans/"
STRATEGY_PREFIX = "strategies/"


@dataclass
class ChangeRecord:
    """一次成功落盘的改动（轮末汇总给前端做跳转 chip）。"""

    area: str        # plan / map_plan / live
    action: str      # add / edit
    ref: str         # 规划 id / 提案标题
    label: str       # 人类可读对象名（如「生产规划 agent-m1」）

    def to_json(self) -> dict:
        return {"area": self.area, "action": self.action, "ref": self.ref,
                "label": self.label, "target": change_target(self.area, self.ref)}


def change_target(area: str, ref: str) -> str:
    """改动 → 前端 hash 路由目标（chip 点击就跳这里；参数由页面消费选中对应对象）。"""
    from urllib.parse import quote

    if area == "plan":
        return f"#/plan-production?plan={quote(ref)}"
    if area == "map_plan":
        return f"#/plan-map?map={quote(ref)}"
    if area == "strategy":
        return "#/plan-flow"   # 规划-策略页（策略文件生效于新会话，页面看的是帧里的图）
    return "#/production"


class ChangeLog:
    """轮内改动的临时收集器。引擎工具与 AgentTalk 同属对话线程，无需锁。"""

    def __init__(self) -> None:
        self._items: list[ChangeRecord] = []

    def add(self, rec: ChangeRecord) -> None:
        self._items.append(rec)

    def drain(self) -> list[ChangeRecord]:
        items, self._items = self._items, []
        return items


def _split(path: str) -> tuple[str, str]:
    """虚拟路径 → (区, id)。plans/xxx.yaml → ('plan', 'xxx')；其余 → ('disk', 原路径)。"""
    if path.startswith(PLAN_PREFIX) and path.endswith(".yaml"):
        pid = path[len(PLAN_PREFIX):-len(".yaml")]
        if pid and "/" not in pid:
            return "plan", pid
    if path.startswith(MAP_PREFIX) and path.endswith(".yaml"):
        pid = path[len(MAP_PREFIX):-len(".yaml")]
        if pid and "/" not in pid:
            return "map", pid
    if path.startswith(STRATEGY_PREFIX) and path.endswith(".yaml"):
        sid = path[len(STRATEGY_PREFIX):-len(".yaml")]
        if sid and "/" not in sid:
            return "strategy", sid
    return "disk", path


def _err_text(exc: ApiError) -> str:
    d = exc.detail
    if isinstance(d, dict) and isinstance(d.get("errors"), list):
        return "；".join(str(e.get("text_zh") or e) for e in d["errors"])
    return str(d)


class ApiWorkspace(Workspace):
    """规划 API + 只读产物 + scratch 磁盘的虚拟文件视图（文件契约工具的存储后端）。

    只实现 5 个存储原语；edit/insert/delete/grep/观察策略（read-before-write）
    由基类基于这些原语组合出来。
    """

    def __init__(self, client: ApiClient, scratch_root: Path,
                 changes: ChangeLog, config: WorkspaceConfig | None = None,
                 readonly: list[ReadOnlyArea] | None = None) -> None:
        super().__init__(config)
        self._client = client
        self._disk = DiskWorkspace(scratch_root)
        self._changes = changes
        self._readonly: list[ReadOnlyArea] = readonly or []

    def _readonly_of(self, path: str) -> ReadOnlyArea | None:
        return next((a for a in self._readonly if a.handles(path)), None)

    # ---- 生产规划：JSON ⇄ YAML ----

    def _render_plan(self, p: dict) -> str:
        doc = {"id": p["id"], "title_zh": p.get("title_zh") or p["id"],
               "map": p.get("map") or "LadderMap", "spawn": p.get("spawn") or "bl",
               "queue": p.get("queue") or []}
        return yaml.safe_dump(doc, allow_unicode=True, sort_keys=False)

    def _save_plan(self, pid: str, content: str) -> None:
        try:
            doc = yaml.safe_load(content) or {}
        except yaml.YAMLError as exc:
            raise WorkspaceError(f"YAML 解析失败：{exc}") from None
        if not isinstance(doc, dict) or not isinstance(doc.get("queue"), list) or not doc["queue"]:
            raise WorkspaceError("生产规划文档必须有非空 queue 列表（op/type/count 每项）")
        # 本地先拦明显错的项（新建路径要先 create 再 PUT，别让残渣留在服务端）
        for i, it in enumerate(doc["queue"]):
            if not isinstance(it, dict) or not it.get("op"):
                raise WorkspaceError(f"queue 第 {i} 项不是带 op 的映射")
        body = {"queue": doc["queue"]}
        for k in ("title_zh", "map", "spawn"):
            if doc.get(k):
                body[k] = doc[k]
        try:
            if not self._plan_exists(pid):
                self._client.plan_create({"id": pid, "title_zh": str(body.get("title_zh") or pid)})
            self._client.plan_save(pid, body)
        except ApiError as exc:
            raise WorkspaceError(f"保存被拒（HTTP {exc.status}）：{_err_text(exc)}") from None

    def _plan_exists(self, pid: str) -> bool:
        try:
            return any(r["id"] == pid for r in self._client.plans_list())
        except ApiError:
            return False

    # ---- 地图规划：doc ⇄ YAML ----

    def _save_map_plan(self, pid: str, content: str) -> None:
        try:
            doc = yaml.safe_load(content) or {}
        except yaml.YAMLError as exc:
            raise WorkspaceError(f"YAML 解析失败：{exc}") from None
        if not isinstance(doc, dict):
            raise WorkspaceError("地图规划文档必须是映射（build_slots/pos_marks）")
        try:
            if not self._map_plan_exists(pid):
                # 新建 = 复制空白预设（与 create_map_plan 缺省一致），再全量 PUT
                self._client.map_plan_create({"id": pid,
                                              "title_zh": str(doc.get("title_zh") or pid)})
            r = self._client.map_plan_save_payload(pid, doc)
        except ApiError as exc:
            raise WorkspaceError(f"保存被拒（HTTP {exc.status}）：{_err_text(exc)}") from None
        if not (r.get("ok") if isinstance(r, dict) else True):
            errs = "；".join(str(e.get("text_zh") or e) for e in (r.get("errors") or []))
            raise WorkspaceError(f"保存被拒：{errs}")

    def _map_plan_exists(self, pid: str) -> bool:
        try:
            return any(r["id"] == pid for r in self._client.map_plans_list())
        except ApiError:
            return False

    # ---- 策略：YAML 两段直读直写（服务端编译期校验） ----

    def _save_strategy(self, sid: str, content: str) -> None:
        try:
            doc = yaml.safe_load(content) or {}
        except yaml.YAMLError as exc:
            raise WorkspaceError(f"YAML 解析失败：{exc}") from None
        if not isinstance(doc, dict) or "strategy" not in doc or "assembly" not in doc:
            raise WorkspaceError(
                "策略文档必须有两段：strategy（id/steps/edges…）与"
                " assembly（groups/strategy_instances）—— 看一个现成的：read strategies/default.yaml")
        try:
            if not self._strategy_exists(sid):
                self._client.strategy_create({"id": sid})
            r = self._client.strategy_save_payload(sid, doc)
        except ApiError as exc:
            raise WorkspaceError(f"保存被拒（HTTP {exc.status}）：{_err_text(exc)}") from None
        if isinstance(r, dict) and not r.get("ok", True):
            errs = "；".join(str(e.get("text_zh") or e) for e in (r.get("errors") or []))
            raise WorkspaceError(f"编译校验未通过：{errs}")

    def _strategy_exists(self, sid: str) -> bool:
        try:
            return any(r["id"] == sid for r in self._client.strategies_list())
        except ApiError:
            return False

    # ---- 存储原语（基类组合出全部文件工具语义） ----

    def _file_exists(self, path: str) -> bool:
        area = self._readonly_of(path)
        if area is not None:
            return area.exists(path)
        area_id, pid = _split(path)
        if area_id == "plan":
            return self._plan_exists(pid)
        if area_id == "map":
            return self._map_plan_exists(pid)
        if area_id == "strategy":
            return self._strategy_exists(pid)
        return self._disk._file_exists(path)

    def _read_file(self, path: str) -> str:
        ro = self._readonly_of(path)
        if ro is not None:
            return ro.read(path)    # 只读区适配器自带错误文案（指路而非干巴巴 404）
        area, pid = _split(path)
        try:
            if area == "plan":
                return self._render_plan(self._client.plan_get(pid))
            if area == "map":
                return yaml.safe_dump(self._client.map_plan_doc(pid),
                                      allow_unicode=True, sort_keys=False)
            if area == "strategy":
                d = self._client.strategy_doc(pid)
                return yaml.safe_dump(
                    {"strategy": d.get("strategy") or {}, "assembly": d.get("assembly") or {}},
                    allow_unicode=True, sort_keys=False)
            return self._disk._read_file(path)
        except ApiError as exc:
            raise WorkspaceError(f"读取失败（HTTP {exc.status}）：{_err_text(exc)}") from None

    def _write_file(self, path: str, content: str) -> None:
        ro = self._readonly_of(path)
        if ro is not None:
            raise WorkspaceError(
                f"{path} 是只读区（历史产物不可改：recordings/ 对局录像、traces/ 会话轨迹、"
                "proposals/ 提案审计史）。要延续结论就写你的 memory/，要改规划就写对应文件。")
        area, pid = _split(path)
        if area == "plan":
            existed = self._plan_exists(pid)
            self._save_plan(pid, content)
            self._changes.add(ChangeRecord(
                area="plan", action="edit" if existed else "add", ref=pid,
                label=f"生产规划 {pid}"))
            return
        if area == "map":
            existed = self._map_plan_exists(pid)
            self._save_map_plan(pid, content)
            self._changes.add(ChangeRecord(
                area="map_plan", action="edit" if existed else "add", ref=pid,
                label=f"地图规划 {pid}"))
            return
        if area == "strategy":
            existed = self._strategy_exists(pid)
            self._save_strategy(pid, content)
            self._changes.add(ChangeRecord(
                area="strategy", action="edit" if existed else "add", ref=pid,
                label=f"策略 {pid}"))
            return
        self._disk._write_file(path, content)

    def _list_file_paths(self, prefix: str = "") -> list[str]:
        out: list[str] = []
        if prefix in ("", "plans", "plans/"):
            try:
                out += [f"{PLAN_PREFIX}{r['id']}.yaml" for r in self._client.plans_list()]
            except ApiError:
                pass
        if prefix in ("", "map-plans", "map-plans/"):
            try:
                out += [f"{MAP_PREFIX}{r['id']}.yaml" for r in self._client.map_plans_list()]
            except ApiError:
                pass
        if prefix in ("", "strategies", "strategies/"):
            try:
                out += [f"{STRATEGY_PREFIX}{r['id']}.yaml" for r in self._client.strategies_list()]
            except ApiError:
                pass
        for area in self._readonly:
            out += area.list_paths(prefix)
        if not (prefix.startswith("plans") or prefix.startswith("map-plans")
                or prefix.startswith("strategies")):
            # scratch 同名路径刻意排除（filter 末句）：只读区是这些前缀的唯一语义，
            # 不给"磁盘上恰好有个 recordings/ 目录"的路径钻空子的机会
            out += [p for p in self._disk._list_file_paths(prefix)
                    if not (p.startswith(PLAN_PREFIX) or p.startswith(MAP_PREFIX)
                            or p.startswith(STRATEGY_PREFIX))
                    and not any(a.handles(p) for a in self._readonly)]
        return sorted(out)

    def _current_version(self, path: str) -> str | None:
        """内容哈希当版本号：read-before-write 的一致性判断不依赖文件系统 mtime。"""
        try:
            return hashlib.md5(self._read_file(path).encode("utf-8")).hexdigest()
        except WorkspaceError:
            return None
