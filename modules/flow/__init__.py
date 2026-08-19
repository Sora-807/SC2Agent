"""flow 模块：manifest + 谓词 + Allocator + engine。

flow 只依赖 game（不 import driver/world/sc2，dep-check 守红线）。
port 用 duck-typing；测试用 driver.fake.FakeGamePort。
"""
from flow.engine import FlowEngine
from flow.manifest import (
    ActionRequest,
    FlowAssembly,
    GroupSpec,
    StrategyInstance,
    StrategyManifest,
    parse_assembly,
    parse_strategy,
)

__all__ = [
    "ActionRequest",
    "FlowEngine",
    "FlowAssembly",
    "GroupSpec",
    "StrategyInstance",
    "StrategyManifest",
    "parse_assembly",
    "parse_strategy",
]
