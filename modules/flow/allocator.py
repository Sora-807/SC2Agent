"""flow Allocator V1：FCFS + sticky lease；group/free 池。

按 spec-006 接口预留（count/expand/create_group/refresh）；V1 简实现，无 share/pool_key 仲裁。
已 lease 的单位不参与重分配；补到 target 从 free 池取；单位死亡从 lease 移除。

词汇（T1/D1）：composition 键与 count/expand 的 type 参数一律是 **stable id**（如 "terran/marine"）；
gs 单位是 burnysc2 实体名（含 SIEGETANKSIEGED 这类形态变体），匹配统一走 catalog 单侧归一。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from game import GameState, Owner

from flow.predicates import unit_is_type


def _refill_floor(spec: dict, target: int) -> int:
    """补兵触发下限（S3 滞回；D6 + H2 边界）。

    - min 省略 → 下限 = target（跌破就补，与滞回前行为一致，不给旧配置换语义）
    - min 给了 → 只有跌破 min 才补回 target（滞回：数量在 [min, target) 不补，
      避免每死一个兵就抢一次 free 池）
    - **min = 0 → 下限取 1**（H2）：字面 0 会让 "len(cur) < 0" 永假、连首次都不填，
      等于静默关掉补兵。0 的意图是"不主动维持"，语义定为"只在空组时补"。
    """
    floor = spec.get("min", target)
    return max(int(floor), 1)


#: 补兵状态取值（前端只做"字符串→颜色"映射，不复算规则）
REFILL_STATES = ("补兵中", "已截断", "滞回区", "满足")


def _refill_state(cur: int, floor: int, target: int, cap: int) -> str:
    """与 `Allocator.refresh` 的分支一一对应，别的地方不要重写这套判断。

    - `cur >= floor`：不补 → 到 target 算"满足"，否则是滞回区（死一个不立刻抢 free 池）
    - `cur < floor`：要补 → need>0 是"补兵中"；need<=0 说明被 max 截断（target>max 的配置）
    """
    if cur >= floor:
        return "满足" if cur >= target else "滞回区"
    return "补兵中" if (min(target, cap) - cur) > 0 else "已截断"


def _worst_state(states: list[str]) -> str:
    """组级状态 = 最"值得关注"的那个类型的状态（补兵中 > 已截断 > 滞回区 > 满足）。"""
    for s in REFILL_STATES:
        if s in states:
            return s
    return "满足"


@dataclass
class GroupState:
    group_id: str
    composition: dict  # stable_id -> {min, target, max}
    leased_by_type: dict = field(default_factory=dict)  # stable_id -> set[tag]


class Allocator:
    def __init__(self, catalog) -> None:
        if catalog is None:
            raise ValueError("Allocator 需要 catalog：composition 用 stable id，匹配 gs 实体名需翻译（T1/D1）")
        self._groups: dict[str, GroupState] = {}
        self._catalog = catalog

    def create_group(self, group_id: str, composition: dict) -> None:
        """建组。composition 键必须是 catalog 已登记的 stable id（未知键构造期即报错，不静默漏 lease）。"""
        unknown = [k for k in composition if self._catalog.by_stable_id(k) is None]
        if unknown:
            raise ValueError(
                f"group {group_id!r} 的 composition 含未登记的 stable id {unknown}"
                "（authoring 侧只用 stable id，如 terran/marine；burnysc2 名不再接受）"
            )
        self._groups[group_id] = GroupState(group_id, composition, {})

    def refresh(self, gs: GameState) -> None:
        own = {u.tag for u in gs.units if u.owner == Owner.SELF}
        # 清死亡
        for g in self._groups.values():
            for t in list(g.leased_by_type):
                g.leased_by_type[t] = {tag for tag in g.leased_by_type[t] if tag in own}
        leased_all = {tag for g in self._groups.values() for s in g.leased_by_type.values() for tag in s}
        free = own - leased_all
        # 补兵（S3 滞回 + FCFS：按 gs.units 顺序取前 N 个 free）
        for g in self._groups.values():
            for stable_id, spec in g.composition.items():
                cur = g.leased_by_type.setdefault(stable_id, set())
                target = spec.get("target", spec.get("max", 0))
                cap = spec.get("max", target)
                floor = _refill_floor(spec, target)
                if len(cur) >= floor:
                    continue  # 滞回区间 [floor, target)：死一个不立刻抢 free 池
                need = min(target, cap) - len(cur)
                if need <= 0:
                    continue
                # 单侧归一：架起后实体名变 SIEGETANKSIEGED，仍匹配 terran/siegetank（T3 语义不变）
                cands = [u.tag for u in gs.units
                         if u.owner == Owner.SELF and u.tag in free
                         and unit_is_type(self._catalog, u.type_name, stable_id)]
                take = set(cands[:need])
                cur |= take
                free -= take

    def count(self, group_id: str, stable_id: str | None = None) -> int:
        g = self._groups.get(group_id)
        if g is None:
            return 0
        if stable_id is None:
            return sum(len(s) for s in g.leased_by_type.values())
        return len(g.leased_by_type.get(stable_id, set()))  # 键就是 stable id，直查（无输入归一）

    def expand(self, group_id: str, stable_id: str) -> list[int]:
        g = self._groups.get(group_id)
        if g is None:
            return []
        return sorted(g.leased_by_type.get(stable_id, set()))

    # ---- 读模型（B1）----
    def snapshot(self) -> list[dict]:
        """每组的 composition（含 current）+ 补兵状态 + 已 lease 单位。

        `refill_state` 的分支**刻意与 `refresh` 一一对应**（同一套 floor/cap/need 判断）：
        补兵规则只有一份实现，UI 显示的状态就不可能和引擎的实际行为对不上。
        前端红线 C3 要求"滞回状态由后端判定"，这里就是那个后端。
        """
        out: list[dict] = []
        for g in self._groups.values():
            comp: dict[str, dict] = {}
            for stable_id, spec in g.composition.items():
                cur = len(g.leased_by_type.get(stable_id, set()))
                target = spec.get("target", spec.get("max", 0))
                cap = spec.get("max", target)
                floor = _refill_floor(spec, target)
                comp[stable_id] = {
                    "min": int(spec.get("min", target)),
                    "target": int(target),
                    "max": int(cap),
                    "current": cur,
                }
                comp[stable_id]["_state"] = _refill_state(cur, floor, target, cap)
            out.append({
                "group_id": g.group_id,
                "composition": {k: {n: v for n, v in spec.items() if n != "_state"}
                                for k, spec in comp.items()},
                "refill_state": _worst_state([spec["_state"] for spec in comp.values()]),
                "leased_tags": sorted({tag for s in g.leased_by_type.values() for tag in s}),
            })
        return out

    def expand_all(self, group_id: str) -> list[int]:
        """group 内所有类型已 lease 的 unit_tag（供 group_center 等空间谓词用）。"""
        g = self._groups.get(group_id)
        if g is None:
            return []
        return sorted({tag for s in g.leased_by_type.values() for tag in s})
