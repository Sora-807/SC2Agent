"""view.plans：离线生产规划的存储（P0）—— **一个规划一个 YAML 文件**（用户拍板）。

规划是 authoring 数据（不是运行态）：地图引用 + 出生点 + 队列三件套。
它同时是人与 agent 的协作载体（用户拍板：不走审批、agent 直接改、diff/撤销兜底），
所以存储必须是后端文件 —— localStorage 里的草稿 agent 读不到。

默认规划 `default`（locked）：从 planner 参考计划 `bio_tank_opening` 导出。
锁定 = 不可覆盖保存、不可删除；要改就复制（与用户对默认地图的同样要求）。

校验口径：队列项复用提案通道的 `parse_item`（同一份 QueueItem 语法），
另加 catalog 存在性；build 的 placement 在规划里**可选** —— planner 不放置
（position 归 live runtime），执行期才需要。
"""
from __future__ import annotations

import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from game.catalog import Catalog
from game.production import QueueItem
from planner.build_order import AssignWorkers, Build, Op, Research, Train
from view.proposals import item_to_json, parse_item

DEFAULT_PLAN_ID = "default"
DEFAULT_PLAN_TITLE = "默认规划（步坦协同开局，锁定）"
#: 从这个参考计划导出默认规划（module_defs 注册名）
DEFAULT_MODULE_REF = "bio_tank_opening"
SPAWNS = ("bl", "tr")

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


@dataclass
class Plan:
    id: str
    title_zh: str
    map: str = "LadderMap"
    spawn: str = "bl"
    queue: list[QueueItem] = field(default_factory=list)
    locked: bool = False
    updated_at: float = 0.0

    def to_json(self) -> dict:
        return {
            "id": self.id, "title_zh": self.title_zh, "map": self.map,
            "spawn": self.spawn, "locked": self.locked,
            "updated_at": self.updated_at,
            # 规划是 authoring 数据：账本字段（uid/status/reason）不落盘（ADR-0032）——
            # uid 是会话内执行轨迹，规划文件跨会话复用
            "queue": [{k: v for k, v in item_to_json(i).items()
                       if k not in ("uid", "status", "reason")} for i in self.queue],
        }

    def meta(self) -> dict:
        return {"id": self.id, "title_zh": self.title_zh, "map": self.map,
                "spawn": self.spawn, "locked": self.locked,
                "items": len(self.queue), "updated_at": self.updated_at}


def ops_to_items(ops: list[Op]) -> list[QueueItem]:
    """planner op 序列 → 队列项（连续同型 Build/Train 合并 count，规划文件可读）。

    AssignWorkers / Research 不合并：前者是目标值语义逐条可读，后者通常每条不同。
    """
    out: list[QueueItem] = []
    for op in ops:
        if isinstance(op, (Build, Train)):
            kind = "build" if isinstance(op, Build) else "train"
            last = out[-1] if out else None
            if (last is not None and last.op.value == kind and last.type == op.type
                    and last.placement is None and last.task is None):
                last.count += 1
                continue
            out.append(parse_item({"op": kind, "type": op.type, "count": 1}))
        elif isinstance(op, AssignWorkers):
            out.append(parse_item({"op": "assign_workers", "task": op.task,
                                   "count": op.count}))
        elif isinstance(op, Research):
            out.append(parse_item({"op": "research", "type": op.type, "count": 1}))
    return out


def resolve_placement_refs(items: list[QueueItem],
                           map_plan_id: str | None) -> tuple[list[QueueItem], str | None]:
    """I8 限定引用「规划名/点位名」→ 裸名（命令面进入会话前的 REST 层映射）。

    - 裸名原样通过（兼容存量：in_region 区域名、预设固定建造点名本来就是全局的）；
    - mark 含 "/"（"agent-m1/rax9"）：会话装的就是该规划 → 剥前缀；装的不是 →
      结构化错误（跨规划引用，槽位名只在规划内唯一）；会话没装规划（出厂模板）
      → 同样拒（没有规划命名空间可对）。
    返回 (新列表, None) 或 (None, 中文理由)。runtime 只见裸名 —— ADR-0027 的
    抽象标记语义不变，ViewFrame 契约零改动。
    """
    from dataclasses import replace

    from game.production import PlacementExact

    out: list[QueueItem] = []
    for i, it in enumerate(items):
        p = it.placement
        if isinstance(p, PlacementExact) and isinstance(p.mark, str) and "/" in p.mark:
            prefix, name = p.mark.split("/", 1)
            if map_plan_id is None:
                return [], (f"第 {i} 项 placement 引用规划 {prefix!r} 的点位，"
                            "但会话装的是出厂模板（无规划命名空间）")
            if prefix != map_plan_id:
                return [], (f"第 {i} 项 placement 引用规划 {prefix!r} 的点位，"
                            f"但会话装的是 {map_plan_id!r}")
            it = replace(it, placement=replace(p, mark=name))
        out.append(it)
    return out, None


class PlanStore:
    """规划文件存储：`{dir}/{id}.yaml`，一规划一文件；dir=None = 纯内存（测试）。"""

    def __init__(self, catalog: Catalog, dir: Path | None) -> None:  # noqa: A002
        self._catalog = catalog
        self._dir = dir
        self._lock = threading.Lock()
        self._plans: dict[str, Plan] = {}
        if dir is not None:
            dir.mkdir(parents=True, exist_ok=True)
            for p in sorted(dir.glob("*.yaml")):
                plan = self._read(p)
                if plan is not None:
                    self._plans[plan.id] = plan
        if DEFAULT_PLAN_ID not in self._plans:
            self._plans[DEFAULT_PLAN_ID] = self._default_plan()
            self._write(self._plans[DEFAULT_PLAN_ID])

    # ---- 默认规划 ----

    def _default_plan(self) -> Plan:
        from planner.build_order import MODULE_REGISTRY

        fn = MODULE_REGISTRY.get(DEFAULT_MODULE_REF)
        assert fn is not None, f"参考计划 {DEFAULT_MODULE_REF} 没注册"
        return Plan(id=DEFAULT_PLAN_ID, title_zh=DEFAULT_PLAN_TITLE, map="LadderMap",
                    spawn="bl", queue=ops_to_items(fn({})), locked=True,
                    updated_at=time.time())

    # ---- 读写 ----

    def _read(self, path: Path) -> Plan | None:
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            return self._from_raw(raw)
        except (OSError, ValueError, yaml.YAMLError):
            return None    # 坏文件跳过（不拖垮整个 store），可删文件名兜底

    def _from_raw(self, raw: dict) -> Plan:
        plan = Plan(
            id=str(raw.get("id") or ""),
            title_zh=str(raw.get("title_zh") or raw.get("id") or ""),
            map=str(raw.get("map") or "LadderMap"),
            spawn=str(raw.get("spawn") or "bl"),
            queue=[parse_item(x) for x in (raw.get("queue") or [])],
            locked=bool(raw.get("locked") or False),
            updated_at=float(raw.get("updated_at") or 0.0),
        )
        self._validate(plan)
        return plan

    def _write(self, plan: Plan) -> None:
        if self._dir is None:
            return
        data = plan.to_json()
        path = self._dir / f"{plan.id}.yaml"
        tmp = path.with_suffix(".tmp")
        tmp.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
                       encoding="utf-8")
        tmp.replace(path)   # 原子落盘：写一半崩不至于毁掉整份规划

    def _validate(self, plan: Plan) -> None:
        if not _ID_RE.match(plan.id):
            raise ValueError(
                f"规划 id {plan.id!r} 不合法（小写字母/数字/-/_，长度 1..64）")
        if plan.spawn not in SPAWNS:
            raise ValueError(f"spawn 必须是 {'/'.join(SPAWNS)}，收到 {plan.spawn!r}")
        for i, it in enumerate(plan.queue):
            op = it.op.value
            if op in ("build", "train", "research"):
                if not it.type:
                    raise ValueError(f"队列第 {i} 项（{op}）缺 type")
                if self._catalog.by_stable_id(it.type) is None:
                    raise ValueError(f"队列第 {i} 项的 type {it.type!r} 不在 catalog")
            if op == "assign_workers" and it.task is None:
                raise ValueError(f"队列第 {i} 项（assign_workers）缺 task")

    # ---- 对外 ----

    def list(self) -> list[dict]:
        with self._lock:
            plans = sorted(self._plans.values(),
                           key=lambda p: (p.id != DEFAULT_PLAN_ID, p.id))
            return [p.meta() for p in plans]

    def get(self, pid: str) -> dict | None:
        with self._lock:
            p = self._plans.get(pid)
            return p.to_json() if p else None

    def save(self, pid: str, raw: dict) -> dict:
        with self._lock:
            old = self._plans.get(pid)
            if old is not None and old.locked:
                raise ValueError(f"规划 {pid} 已锁定（默认规划）：复制一份再改")
            raw = {"id": pid, **raw}
            plan = self._from_raw(raw)
            plan.updated_at = time.time()
            self._plans[pid] = plan
            self._write(plan)
            return plan.to_json()

    def create(self, raw: dict) -> dict:
        """新建：`copy_from` 复制既有规划（默认路径），否则空队列。id 缺省自动生成。"""
        with self._lock:
            pid = str(raw.get("id") or f"plan-{uuid.uuid4().hex[:6]}")
            if pid in self._plans:
                raise ValueError(f"规划 id {pid!r} 已存在")
            src = self._plans.get(str(raw.get("copy_from") or "")) if raw.get("copy_from") else None
            if raw.get("copy_from") and src is None:
                raise ValueError(f"要复制的规划 {raw.get('copy_from')!r} 不存在")
            plan = Plan(
                id=pid,
                title_zh=str(raw.get("title_zh") or (src.title_zh if src else "新规划")),
                map=str(raw.get("map") or (src.map if src else "LadderMap")),
                spawn=str(raw.get("spawn") or (src.spawn if src else "bl")),
                queue=list(src.queue) if src else
                [parse_item(x) for x in (raw.get("queue") or [])],
                locked=False, updated_at=time.time())
            self._validate(plan)
            self._plans[pid] = plan
            self._write(plan)
            return plan.to_json()

    def remove(self, pid: str) -> None:
        with self._lock:
            p = self._plans.get(pid)
            if p is None:
                raise KeyError(pid)
            if p.locked:
                raise ValueError(f"规划 {pid} 已锁定，不能删除（默认规划）")
            del self._plans[pid]
            if self._dir is not None:
                (self._dir / f"{pid}.yaml").unlink(missing_ok=True)
