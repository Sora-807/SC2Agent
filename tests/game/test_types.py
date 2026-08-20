"""P2 类型冒烟：能构造、字段可访问、默认值正确。"""
from game import (
    Cost,
    GameState,
    Grid,
    GridPos,
    Operation,
    Order,
    Owner,
    Point2,
    Queue,
    QueueItem,
    QueueOp,
    RawGameState,
    RawOrder,
    RawUnit,
    Role,
    Unit,
    WorkerTask,
)


def test_point2_grid_immutable():
    p = Point2(1.0, 2.0)
    assert p.x == 1.0 and p.y == 2.0
    g = Grid(2, 2, [[0, 1], [1, 0]])
    assert g.width == 2 and g.height == 2 and g.data[0][1] == 1


def test_point2_grid_properties():
    p = Point2(1.5, 2.7)
    assert p.grid_x == 1          # int(1.5) = 1
    assert p.grid_y == 2          # int(2.7) = 2
    assert p.grid_pos == GridPos(1, 2)
    assert p.grid_pos.x == 1 and p.grid_pos.y == 2
    # 整数坐标
    assert Point2(3.0, 4.0).grid_pos == GridPos(3, 4)


def test_raw_state_construct():
    u = RawUnit(
        tag=1,
        type_name="SCV",
        position=Point2(48.5, 25.5),
        alliance=1,
        health=45.0,
        health_max=45.0,
        shield=0.0,
        energy=0.0,
        build_progress=1.0,
        orders=[RawOrder(ability_name="Gather")],
    )
    cells = [[0]] * (176 * 160)
    s = RawGameState(
        seq=0,
        game_time=0.0,
        minerals=50,
        vespene=0,
        supply_used=8,
        supply_cap=13,
        units=[u],
        map_size=(176, 160),
        creep=Grid(176, 160, cells),
        visibility=Grid(176, 160, cells),
    )
    assert s.units[0].type_name == "SCV"
    assert s.units[0].alliance == 1
    assert s.units[0].orders[0].ability_name == "Gather"
    assert s.creep.width == 176


def test_operation():
    op = Operation(op_id=1, unit_tags=[1, 2], action="move_to", params={"position": (1, 2)}, seq=5)
    assert op.action == "move_to" and op.unit_tags == [1, 2]


def test_queue_item_defaults():
    qi = QueueItem(op="train", type="terran/marine")
    assert qi.count == 1 and qi.task is None and qi.when is None
    assert qi.op is QueueOp.TRAIN  # 字符串构造自动归一化为枚举成员
    q = Queue(name="opening", items=[qi])
    assert q.items[0].count == 1


def test_queue_item_op_and_task_coercion():
    qi = QueueItem(op="assign_workers", task="gas")
    assert qi.op is QueueOp.ASSIGN_WORKERS
    assert qi.task is WorkerTask.GAS
    assert WorkerTask.MINERAL == "mineral"  # str 基类：与字符串比较不破坏既有习惯


def test_cost_dataclass():
    c = Cost(minerals=50, vespene=0, supply=1)
    assert c.minerals == 50 and c.vespene == 0 and c.supply == 1
    assert Cost() == Cost(minerals=0, vespene=0, supply=0)  # 默认零成本


def test_role_enum():
    assert Role.WORKER == "worker"
    assert Role("combat") is Role.COMBAT
    assert [r.value for r in Role] == ["worker", "combat", "building", "upgrade"]


def test_gamestate_owner_enum():
    u = Unit(
        tag=1,
        type_name="terran/scv",
        position=Point2(0.0, 0.0),
        owner=Owner.SELF,
        hp=45.0,
        hp_max=45.0,
        shield=0.0,
        energy=0.0,
        build_progress=1.0,
        orders=[Order(ability="gather")],
    )
    assert u.owner == Owner.SELF
    gs = GameState(
        seq=0, game_time=0.0, minerals=50, vespene=0, supply_used=8, supply_cap=13,
        units=[u], map_size=(176, 160), creep=Grid(1, 1, [[0]]), visibility=Grid(1, 1, [[0]]),
    )
    assert gs.units[0].owner is Owner.SELF
