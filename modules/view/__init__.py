"""view：只读视图层 —— 把运行时状态编成 ViewFrame（契约见 docs/contract/plan-frontend.md §2）。

位置：flow / production / planner / tactical_map **之上**；没有任何下层模块 import view
（tests/architecture/test_imports.py 锁死）。因此 view 可以随 UI 需要演进，不会反向污染引擎。

分工：
- `schema`  只依赖 game 的数据模型（可独立于任何重构演进）
- `encode`  dataclass → JSON + 栅格 base64
- `statics` 静态面（map / catalog / schema）
- `adapt`   动态面（world / projection / session）
- `jsonl`   帧序列读写（复盘素材与夹具同一格式）
"""
from view.schema import REV, STATIC_TOPICS, TOPICS

__all__ = ["REV", "TOPICS", "STATIC_TOPICS"]
