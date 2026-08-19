"""flow Allocator V1：FCFS + sticky lease；group/free 池。

按 spec-006 接口预留（count/expand/create_group/refresh）；V1 简实现，无 share/pool_key 仲裁。
已 lease 的单位不参与重分配；补到 target 从 free 池取；单位死亡从 lease 移除。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from game import GameState, Owner


@dataclass
class GroupState:
    group_id: str
    composition: dict  # type -> {min, target, max}
    leased_by_type: dict = field(default_factory=dict)  # type -> set[tag]


class Allocator:
    def __init__(self) -> None:
        self._groups: dict[str, GroupState] = {}

    def create_group(self, group_id: str, composition: dict) -> None:
        self._groups[group_id] = GroupState(group_id, composition, {})

    def refresh(self, gs: GameState) -> None:
        own = {u.tag for u in gs.units if u.owner == Owner.SELF}
        # 清死亡
        for g in self._groups.values():
            for t in list(g.leased_by_type):
                g.leased_by_type[t] = {tag for tag in g.leased_by_type[t] if tag in own}
        leased_all = {tag for g in self._groups.values() for s in g.leased_by_type.values() for tag in s}
        free = own - leased_all
        # 补到 target（FCFS：按 gs.units 顺序取前 N 个 free）
        for g in self._groups.values():
            for type_name, spec in g.composition.items():
                cur = g.leased_by_type.setdefault(type_name, set())
                target = spec.get("target", spec.get("max", 0))
                need = target - len(cur)
                if need <= 0:
                    continue
                cands = [u.tag for u in gs.units
                         if u.type_name == type_name and u.owner == Owner.SELF and u.tag in free]
                take = set(cands[:need])
                cur |= take
                free -= take

    def count(self, group_id: str, type_name: str | None = None) -> int:
        g = self._groups.get(group_id)
        if g is None:
            return 0
        if type_name is None:
            return sum(len(s) for s in g.leased_by_type.values())
        return len(g.leased_by_type.get(type_name, set()))

    def expand(self, group_id: str, type_name: str) -> list[int]:
        g = self._groups.get(group_id)
        if g is None:
            return []
        return sorted(g.leased_by_type.get(type_name, set()))

    def expand_all(self, group_id: str) -> list[int]:
        """group 内所有类型已 lease 的 unit_tag（供 group_center 等空间谓词用）。"""
        g = self._groups.get(group_id)
        if g is None:
            return []
        return sorted({tag for s in g.leased_by_type.values() for tag in s})
