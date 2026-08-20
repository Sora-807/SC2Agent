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
    """生产队列项（planner 仿真消费）。"""


@dataclass
class Build(Op):
    type: str                       # 稳定 ID "terran/factory"


@dataclass
class Train(Op):
    type: str                       # 稳定 ID "terran/siegetank"


@dataclass
class AssignWorkers(Op):
    task: str                       # "mineral" / "gas"
    count: int


@dataclass
class Research(Op):
    type: str                       # 稳定 ID "terran/infantryweapons1"


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


def expand(seq: list[ProductionModuleInstance]) -> list[Op]:
    """把 production_sequence（模块实例列表）展平成 op 序列（按实例顺序拼接）。"""
    out: list[Op] = []
    for inst in seq:
        fn = MODULE_REGISTRY.get(inst.module_ref)
        if fn is None:
            raise ValueError(
                f"未知 production module {inst.module_ref!r}（instance {inst.instance_id!r}）")
        out.extend(fn(inst.params))
    return out
