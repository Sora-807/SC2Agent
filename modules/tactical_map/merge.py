"""tactical_map.merge：会话图层合并（PLAN-V2 批 2，ADR-0033）。

一份图层，三种解析：
- **默认规划 = 裸名**：其槽位就是 `home` 区的 build_slots 名单 —— placement
  null=auto 的自动放置按声明序消费它；热切默认 = 换这份名单。
- **全部规划 = `规划id/名字` 命名空间键**：显式引用（PlacementExact 的 mark
  带 "/"）不受默认热切影响 —— 引用钉在规划上，不钉在"当前默认"上。
- **预设固定建造点 = 全局裸名**（reserved_marks：蓝方主矿气井1…，与装载哪份
  规划无关）。

同一出生端平移：所有规划都按**同一个 spawn_key** 的分支实例化到同一个实际 CC
（出生点随机，分支由 pick_spawn_layout 定）。
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from game import Point2
from tactical_map.base import BaseTemplate, instantiate_spawn, load_ladder_map, load_map_plan
from tactical_map.region import RegionLayer


def load_plan_templates(dir: Path | str | None) -> dict[str, BaseTemplate]:
    """目录下全部 `*.yaml` → {规划id: BaseTemplate}（单/双分支文件都吃，load_map_plan 兼容）。

    目录不存在/None = 空（调用方用出厂模板兜底）。
    """
    if dir is None:
        return {}
    d = Path(dir)
    if not d.is_dir():
        return {}
    out: dict[str, BaseTemplate] = {}
    for p in sorted(d.glob("*.yaml")):
        try:
            out[p.stem] = load_map_plan(p)
        except Exception:  # noqa: BLE001 —— 一个坏文件不该弄死整个会话装配
            continue
    return out


@dataclass(frozen=True, slots=True)
class MergedLayer:
    """合并结果：layer 给 runtime/引擎用；default_id/spawn_key 给热切与诊断。"""

    layer: RegionLayer
    default_id: str | None    # None = 出厂模板兜底（会话没选默认规划）
    spawn_key: str


def merged_layer(
    templates: dict[str, BaseTemplate],
    default_id: str | None,
    spawn_key: str,
    cc: Point2,
    *,
    reserved_marks: dict | None = None,
    map_size: tuple[int, int] = (176, 160),
) -> MergedLayer:
    """装配会话图层（详见模块 docstring 的三种解析）。

    `default_id` 不在 templates/None → 出厂 `load_ladder_map()` 当默认（现状兼容）。
    """
    default = templates.get(default_id) if default_id else None
    if default is None or spawn_key not in default.spawns:
        # 请求的默认规划不存在（或该侧没有分支）→ 出厂模板兜底，默认身份清空
        default = load_ladder_map()
        default_id = None
    if spawn_key not in default.spawns:
        spawn_key = sorted(default.spawns)[0]

    base = instantiate_spawn(default, default.spawns[spawn_key], cc, map_size=map_size)

    # 命名空间键：全部规划（含默认自己 —— 显式引用钉在规划上，热切不换走它）。
    # 键和槽位对象的名字都要带前缀 —— map_static 按 slot.name 渲染，名字不带
    # 前缀的话三个规划的同名槽会在 payload 里互相覆盖成裸名重复。
    from dataclasses import replace as _r

    ns_slots = dict(base.build_slots)
    ns_marks = dict(base.pos_marks)
    for pid, tpl in templates.items():
        layout = tpl.spawns.get(spawn_key)
        if layout is None:
            continue  # 单分支规划只在自己那一侧有布局：另一侧没有它的份，如实
        sub = instantiate_spawn(tpl, layout, cc, map_size=map_size)
        for name, slot in sub.build_slots.items():
            ns_slots[f"{pid}/{name}"] = _r(slot, name=f"{pid}/{name}")
        for name, mark in sub.pos_marks.items():
            ns_marks[f"{pid}/{name}"] = _r(mark, name=f"{pid}/{name}")

    base.build_slots.update(ns_slots)
    base.pos_marks.update(ns_marks)
    if reserved_marks:
        base.pos_marks.update(reserved_marks)   # 预设固定点：全局裸名
    return MergedLayer(layer=base, default_id=default_id, spawn_key=spawn_key)
