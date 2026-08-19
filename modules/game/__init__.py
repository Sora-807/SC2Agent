"""game 模块：全项目共享地基（唯一零依赖模块；不 import sc2，红线 R2/ADR-0001）。

业务职责（docs/需求文档-v0.1.md §1 模块表；ADR-0002）：
- geometry：坐标/网格原语（左下原点约定）
- raw：SC2 原生形态状态（driver 产、world 消费）
- state：处理后形态状态（world 产、flow/constraint/planner 消费）
- operation：driver 边界动作 Operation + 操作目录 OP_CATALOG
- production：生产队列 schema
- ports：driver ↔ engine 边界契约（GamePort/RuntimeSink/ApplyResult/事件）
- catalog：单位/建筑数据注册表（JSON 加载，稳定 ID ↔ burnysc2 名映射）
"""
from game.catalog import (
    KNOWN_CAPABILITIES,
    KNOWN_RACES,
    Catalog,
    CatalogEntry,
    Cost,
    Role,
    load_terran,
)
from game.geometry import Grid, GridPos, Point2
from game.operation import OP_CATALOG, Operation, ParamType, is_known_action, validate_op
from game.ports import ApplyResult, GameEvent, GamePort, RuntimeSink, SessionEvent
from game.production import (
    PlacementExact,
    PlacementInRegion,
    PlacementSpec,
    Queue,
    QueueItem,
    QueueOp,
    WorkerTask,
)
from game.raw import RawGameState, RawOrder, RawUnit
from game.state import GameState, Order, Owner, Unit

__all__ = [
    "ApplyResult",
    "Catalog",
    "CatalogEntry",
    "Cost",
    "GameEvent",
    "GamePort",
    "GameState",
    "Grid",
    "GridPos",
    "KNOWN_CAPABILITIES",
    "KNOWN_RACES",
    "OP_CATALOG",
    "Operation",
    "Order",
    "Owner",
    "ParamType",
    "PlacementExact",
    "PlacementInRegion",
    "PlacementSpec",
    "Point2",
    "Queue",
    "QueueItem",
    "QueueOp",
    "RawGameState",
    "RawOrder",
    "RawUnit",
    "Role",
    "RuntimeSink",
    "SessionEvent",
    "Unit",
    "WorkerTask",
    "is_known_action",
    "load_terran",
    "validate_op",
]
