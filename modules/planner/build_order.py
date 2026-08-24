"""planner.build_order：Op 模型 + production_sequence 展开。

对齐 P0：production_sequence = 模块实例列表（instance_id/module_ref/version/params）；
每个 module_ref → op 序列（参数化）。expand() 把整个 production_sequence 展平成 op 序列供仿真消费。

Op 词表（V1）：build / train / assign_workers / research（cancel/morph 后补）。
type 用稳定 ID（如 "terran/factory"），仿真只数建筑数不放置（position 归 live runtime）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable


@dataclass
class Op:
    """生产队列项（planner 仿真消费）。

    uid（PLAN-V2 批 3）：来源队列项的账本 ID，声明在各子类末位（基类放默认值
    会挡住子类的非默认字段）—— count 展开的多个 Op 共享同一 uid（状态表按队列
    项归并）。None = 模块注册产物/无队列来源。
    """


@dataclass
class Build(Op):
    type: str                       # 稳定 ID "terran/factory"
    uid: str | None = None


@dataclass
class Train(Op):
    type: str                       # 稳定 ID "terran/siegetank"
    uid: str | None = None


@dataclass
class AssignWorkers(Op):
    task: str                       # "mineral" / "gas"
    count: int
    uid: str | None = None


@dataclass
class Research(Op):
    type: str                       # 稳定 ID "terran/infantryweapons1"
    uid: str | None = None


@dataclass
class ProductionModuleInstance:
    """production_sequence 的一项（对齐 P0 L127）。"""

    instance_id: str
    module_ref: str
    version: int = 1
    params: dict = field(default_factory=dict)


# module_ref → (params → op 序列)。V1 用 code 函数注册；YAML authoring 后续。
MODULE_REGISTRY: dict[str, Callable[[dict], list[Op]]] = {}


def register_module(ref: str, fn: Callable[[dict], list[Op]]) -> None:
    """登记一个 production module（module_ref → params → op 序列）。重复注册（同 ref）= 坏数据报错。"""
    if ref in MODULE_REGISTRY:
        raise ValueError(f"production module {ref!r} 重复注册")
    MODULE_REGISTRY[ref] = fn


def expand(seq: list) -> list[Op]:
    """把 production_sequence 展平成 op 序列（按顺序拼接）。

    **已展开的 `Op` 原样透传**：这样投影既能吃"模块实例列表"（authoring 面），
    也能吃"已经是 op 序列"的输入 —— 比如把 live 生产队列（`QueueItem`）翻成 Op 后直接投影
    （见 `view.projection`）。否则"当前队列的实时投影"只能靠另建一条模块注册的歪路。
    混合列表也允许（一部分模块实例、一部分裸 Op）。
    """
    out: list[Op] = []
    for item in seq:
        if isinstance(item, Op):
            out.append(item)
            continue
        fn = MODULE_REGISTRY.get(item.module_ref)
        if fn is None:
            raise ValueError(
                f"未知 production module {item.module_ref!r}（instance {item.instance_id!r}）")
        out.extend(fn(item.params))
    return out
