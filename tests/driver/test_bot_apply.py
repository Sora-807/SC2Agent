"""SC2DriverBot 应用层单测：op_queue drain → translate → self.do（下一 step 生效）+ 异常降级。

用 object.__new__(SC2DriverBot) 绕过 BotAI.__init__（无需连 SC2），注入 do 记录器。
"""
from types import SimpleNamespace

from driver.sc2_adapter import SC2DriverBot
from game import Grid, Operation, Point2, RawGameState, RawUnit


class FakeUnit:
    """与 test_translate 同款的记录假单位；move 可注入抛错模拟命令失败。

    同时带 extract_raw_unit 需要的 duck 属性（on_step 抽取用）。
    """

    def __init__(self, tag, fail_move=False):
        self.tag = tag
        self.fail_move = fail_move
        self.type_id = SimpleNamespace(name="SCV")
        self.position = SimpleNamespace(x=0.0, y=0.0)
        self.alliance = 1
        self.health = 45.0
        self.health_max = 45.0
        self.shield = 0.0
        self.energy = 0.0
        self.build_progress = 1.0
        self.orders = ()
        self.facing = 0.0
        self.buffs = ()
        self.is_carrying_minerals = False
        self.is_carrying_vespene = False

    def move(self, p, queue=False):
        if self.fail_move:
            raise RuntimeError("move failed")
        return ("move", self.tag, p)

    def hold_position(self, queue=False): return ("hold", self.tag)
    def stop(self, queue=False): return ("stop", self.tag)


def _bot(units, do_log, op_queue=None):
    bot = object.__new__(SC2DriverBot)  # 不触发 BotAI.__init__
    bot.all_units = units
    bot._op_queue = list(op_queue or [])
    bot._sink = None
    bot._last_raw = None
    bot.do = lambda cmd: do_log.append(cmd) or None
    return bot


def _op(action, unit_tags, **params):
    return Operation(op_id=1, unit_tags=unit_tags, action=action, params=params, seq=0)


def test_drain_applies_ops_in_fifo_order():
    """submit 顺序 = 应用顺序（队列 FIFO），每条 op 翻译成对应命令下发。"""
    units = [FakeUnit(1), FakeUnit(2)]
    do_log = []
    bot = _bot(units, do_log, op_queue=[
        _op("move_to", [1, 2], position=[1, 1]),
        _op("hold_position", [1]),
    ])
    bot._drain_ops()
    assert do_log[0] == ("move", 1, do_log[0][2])
    assert do_log[1] == ("move", 2, do_log[0][2])
    assert do_log[2] == ("hold", 1)
    assert bot._op_queue == []  # 队列清空


def test_apply_swallows_command_failure_and_continues():
    """单条命令失败：静默跳过（D6/V1），不影响队列里其余 op（R7 不崩游戏）。"""
    units = [FakeUnit(1, fail_move=True), FakeUnit(2)]
    do_log = []
    bot = _bot(units, do_log, op_queue=[
        _op("move_to", [1, 2], position=[1, 1]),
        _op("stop", [2]),
    ])
    bot._drain_ops()
    assert do_log == [("stop", 2)]  # 第一条失败被吞，第二条照常执行


def test_apply_noop_when_unit_missing():
    """unit_tags 全部找不到 → 无命令下发，队列照常消费。"""
    do_log = []
    bot = _bot([FakeUnit(1)], do_log, op_queue=[_op("move_to", [99], position=[1, 1])])
    bot._drain_ops()
    assert do_log == []


async def test_on_step_extracts_state_then_drains_ops():
    """on_step = 抽 RawGameState 推 sink → drain 队列（对应下一 step 生效）。"""
    fake_state = SimpleNamespace(
        creep=SimpleNamespace(width=2, height=2, data=b"\x00\x01\x01\x00"),
        visibility=SimpleNamespace(width=2, height=2, data=b"\x00\x01\x01\x00"),
    )
    received: list[RawGameState] = []

    class _Sink:
        def on_game_state(self, raw: RawGameState) -> None:
            received.append(raw)

        def on_session_event(self, event) -> None:
            pass

    do_log = []
    bot = object.__new__(SC2DriverBot)
    bot.all_units = [FakeUnit(1)]
    bot.state = fake_state
    bot.game_info = SimpleNamespace(map_size=(2, 2))
    # 注：time/minerals/... 是 BotAI 只读 property（无对局数据时 getattr 走默认值），
    # extract_raw_state 的 duck 抽取对它们取默认 0.0/0，不影响本测试断言的字段。
    bot._sink = _Sink()
    bot._last_raw = None
    bot._op_queue = [_op("move_to", [1], position=[0.5, 0.5])]
    bot.do = lambda cmd: do_log.append(cmd) or None

    await bot.on_step(0)

    assert len(received) == 1
    assert received[0].seq == 0 and received[0].units[0].tag == 1
    assert len(do_log) == 1 and do_log[0][0] == "move"
    assert bot._last_raw is not None  # read() 的数据源


def test_last_raw_is_read_source():
    """read() 语义：返回 on_step 抽取的最近一帧（SC2GamePort.read 的数据源）。"""
    bot = _bot([FakeUnit(1)], [])
    bot._last_raw = RawGameState(
        seq=3, game_time=1.0, minerals=50, vespene=0, supply_used=8, supply_cap=13,
        units=[RawUnit(tag=1, type_name="SCV", position=Point2(0, 0), alliance=1,
                       health=45.0, health_max=45.0, shield=0.0, energy=0.0, build_progress=1.0)],
        map_size=(2, 2), creep=Grid(1, 1, [[0]]), visibility=Grid(1, 1, [[0]]),
    )
    assert bot._last_raw.seq == 3


async def test_on_step_sinks_first_state_before_map_info_callback():
    """地形回调必须排在首帧 sink **之后**（真机「地形不可用」的根因锁，2026-08-22）。

    run_session 的 Session.on_state 由 sink 首帧触发、同步发出 static/map 等静态面；
    地形控制行若排在它前面，LiveSession 的静态缓冲顺序就是 [terrain, map, ...]，
    前端 store 的 terrain→map 合并（map 未到即丢）会吞掉整局地形 ——
    sim 侧当年为同一顺序修过（B16 教训），driver 这条漏了。
    """

    class _Arr:
        def __init__(self, data):
            self._data = data

        def tolist(self):
            return [list(r) for r in self._data]

    class _PixelMap:
        def __init__(self, data, w, h):
            self.data_numpy = _Arr(data)
            self.width = w
            self.height = h

    events: list[str] = []

    class _Sink:
        def on_game_state(self, raw: RawGameState) -> None:
            events.append("sink")

        def on_session_event(self, event) -> None:
            pass

    bot = object.__new__(SC2DriverBot)
    bot.all_units = [FakeUnit(1)]
    bot.state = SimpleNamespace(creep=None, visibility=None)
    bot.game_info = SimpleNamespace(
        map_size=(2, 2),
        terrain_height=_PixelMap([[1, 2], [3, 4]], 2, 2),
        pathing_grid=_PixelMap([[1, 0], [1, 0]], 2, 2),
        placement_grid=_PixelMap([[0, 1], [1, 1]], 2, 2),
    )
    bot._sink = _Sink()
    bot._last_raw = None
    bot._op_queue = []
    bot._map_info_cb = lambda info: events.append("terrain")

    await bot.on_step(0)
    assert events == ["sink", "terrain"]            # 顺序就是本测试的全部
    await bot.on_step(1)
    assert events == ["sink", "terrain", "sink"]    # 地形只发一次（_map_info_sent）

