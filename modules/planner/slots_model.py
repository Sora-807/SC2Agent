"""planner.slots_model：仿真侧的放置近似模型（PLAN-V2 批 2 漏账，2026-08-24 补）。

live 运行时按合并图层全解析（ADR-0033）；仿真没有图层 —— 这里用**默认图层
（出厂模板或指定地图规划）的 home 区槽位表**做近似：

- 槽位按（类别， 尺寸）过滤、声明序消耗 —— 与 production/placement.py 的
  in_region 自动找位同一套规则；
- `exact` 引用按槽位名占用（同 mark 二次占用 = 冲突）；标记不存在 = 作者错误
  （在 queue_to_ops 翻译层摘除进「未入仿」，仿真继续 —— D6 分工）；
- 挂件（贴母建筑）与气矿（建在气井）不占槽 —— 近似不建模母建筑容量；
- spawn 取 bl（标准开局近似；真机 tr 局的图层是镜像，槽位数同构）。

耗尽 → classify 闭集的 placement_collision（批 1 就绪的 skip key 在仿真侧
终于有了真值来源）。
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class _Slot:
    name: str
    kind: str          # supply / production / addon
    size: int
    taken: bool = False


class SlotPool:
    """不可变槽位表 + 消耗账本（peek 不动账 / take 占位）。"""

    def __init__(self, slots: list[_Slot], *, source_id: str | None = None,
                 source_label: str = "出厂模板") -> None:
        self._slots = slots
        self._by_name = {s.name: s for s in slots}
        self.source_id = source_id           # 命名空间引用剥前缀用（None=出厂）
        self.source_label = source_label     # 校验/报告文案用

    @classmethod
    def from_template(cls, template, spawn: str = "bl",
                      source_id: str | None = None) -> "SlotPool | None":
        """BaseTemplate 的某出生分支 → SlotPool（无该分支 = None = 不建模）。"""
        layout = template.spawns.get(spawn)
        if layout is None:
            return None
        label = source_id or "出厂模板"
        return cls([_Slot(s.name, s.kind, s.size) for s in layout.build_slots],
                   source_id=source_id, source_label=f"{label}（{spawn} 侧，近似）")

    def marks(self) -> frozenset[str]:
        return frozenset(self._by_name)

    @staticmethod
    def handles(entry) -> bool:
        """该类建筑是否走槽位：挂件（贴母建筑）/气矿（建气井）不占槽。"""
        caps = set(entry.capabilities) if entry is not None else set()
        return not ({"addon", "gas"} & caps)

    def _want(self, entry) -> tuple[str, int | None]:
        caps = set(entry.capabilities)
        kind = "supply" if "supply" in caps else "production"
        return kind, entry.size

    def peek(self, entry, mark: str | None = None) -> str | None:
        """None = 有位可放；str = 中文失败原因（不消耗）。"""
        if not self.handles(entry):
            return None
        if mark is not None:
            s = self._by_name.get(mark)
            if s is None:
                return f"槽位 {mark!r} 不在图层里（作者错误：改名或换图层）"
            if s.taken:
                return f"槽位 {mark!r} 已被更早的建造占用（placement_collision）"
            return None
        kind, size = self._want(entry)
        for s in self._slots:
            if not s.taken and s.kind == kind and (size is None or s.size == size):
                return None
        return f"无可用 {kind} 槽位（图层耗尽，placement_collision）"

    def take(self, entry, mark: str | None = None) -> str | None:
        """占用一个槽位；失败原因同 peek（失败不占）。"""
        err = self.peek(entry, mark)
        if err is not None:
            return err
        if not self.handles(entry):
            return None
        if mark is not None:
            self._by_name[mark].taken = True
            return None
        kind, size = self._want(entry)
        for s in self._slots:
            if not s.taken and s.kind == kind and (size is None or s.size == size):
                s.taken = True
                return None
        return None   # pragma: no cover —— peek 已保证有位
