"""production 模块：生产运行时（生产半边 —— 模块审查时代的最大缺口，现状与演进见 docs/REFACTOR.md）。

- runtime：命名队列 drain（队首 constraint 门控）+ agent 工具操作（submit/append/prepend/clear/remove/reorder）
- worker：WorkerAllocator —— assign_workers 展开（矿2气3饱和 + idle 解放）
依赖：game / constraint / tactical_map（placement 解析）；不 import driver/sc2（port duck-typing）。
"""
from production.runtime import ProductionRuntime, UNSUPPORTED_QUEUE_OPS
from production.worker import Emission, WorkerAllocator

__all__ = ["Emission", "ProductionRuntime", "UNSUPPORTED_QUEUE_OPS", "WorkerAllocator"]
