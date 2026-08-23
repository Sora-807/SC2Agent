"""WorldSim 产槽模型（I10 回归锁）。

90 秒夹具曾出现"一个兵营同时爬 6 条机枪兵进度条"：矿量根本撑不住的并行开工
被当成录制真值投进泳道图，用户当场质疑（ISSUES I10）。根因是 `_op_train`
没有产槽占用检查 —— 真机 SC2 的训练是排队的：命令立刻接受，进度条等前一个
训完才起算。这里锁死排队语义。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "modules"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))

from game import Operation, Owner, Point2  # noqa: E402
from game.catalog import load_all  # noqa: E402
from worldsim import WorldSim  # noqa: E402

CAT = load_all()


def _barracks_world(minerals: float = 1000.0) -> tuple[WorldSim, int]:
    w = WorldSim(catalog=CAT, minerals=minerals)
    w.bootstrap(workers=6)
    rax = w._spawn("terran/barracks", Point2(w.cc_pos.x + 4, w.cc_pos.y + 4), ready=True)  # noqa: SLF001
    return w, rax.tag


def _train(w: WorldSim, rax_tag: int) -> None:
    w.apply([Operation(op_id=0, unit_tags=[rax_tag], action="train",
                       params={"type": "terran/marine"}, seq=0)])


def test_train_queues_per_producer_not_parallel():
    """同帧/连续发 3 条 train：完成时刻必须 18s 链式排开，不是同时到点。"""
    w, rax = _barracks_world()
    bt = float(CAT.by_stable_id("terran/marine").build_time)
    for _ in range(3):
        _train(w, rax)
    finishes = sorted(p.finish_t for p in w._pending)  # noqa: SLF001
    assert finishes == [bt, 2 * bt, 3 * bt], "三条 train 应链式排队（I10）"


def test_supply_cap_single_source_matches_economy():
    """供给增量单一真相源 = planner.economy.supply_provided（本机 dump+录像：CC=13）。

    REFACTOR B5：worldsim 曾写死 bases*15、opening 种子写 15、economy 写 13 ——
    三份拷贝互相矛盾。这里锁"开局 1 CC 的 cap 就是 economy 给的值"，
    谁再写死一份拷贝当场红。
    """
    from planner.economy import DEFAULT_ECON
    from planner.opening import CC_SUPPLY, opening_game_state

    cc = DEFAULT_ECON.supply_provided["terran/commandcenter"]
    assert CC_SUPPLY == cc, "opening 种子必须取自 economy（不许第二份拷贝）"
    assert opening_game_state(CAT).supply_cap == cc

    w = WorldSim(catalog=CAT)
    w.bootstrap(workers=6)
    assert w.game_state().supply_cap == cc


def test_queued_unit_progress_stays_low_until_its_turn():
    """排队中的单位进度停在低位：第二条 train 在第一条训完前不该爬进度。"""
    w, rax = _barracks_world()
    bt = float(CAT.by_stable_id("terran/marine").build_time)
    _train(w, rax)
    _train(w, rax)
    for _ in range(int(bt) - 1):
        w.tick()
    gs = w.game_state()
    marines = [u for u in gs.units
               if u.owner is Owner.SELF and u.type_name == "MARINE"]
    assert len(marines) == 2
    first = max(marines, key=lambda u: u.build_progress)
    second = min(marines, key=lambda u: u.build_progress)
    assert first.build_progress > 0.8
    assert second.build_progress <= 0.1, "排队的第二条不该提前爬进度（I10）"


def test_different_producers_train_in_parallel():
    """两个兵营各自排队：互不占用（产槽是 per-producer 的）。"""
    w = WorldSim(catalog=CAT, minerals=1000.0)
    w.bootstrap(workers=6)
    a = w._spawn("terran/barracks", Point2(w.cc_pos.x + 4, w.cc_pos.y + 4), ready=True)  # noqa: SLF001
    b = w._spawn("terran/barracks", Point2(w.cc_pos.x + 8, w.cc_pos.y + 8), ready=True)  # noqa: SLF001
    _train(w, a.tag)
    _train(w, b.tag)
    bt = float(CAT.by_stable_id("terran/marine").build_time)
    finishes = sorted(p.finish_t for p in w._pending)  # noqa: SLF001
    assert finishes == [bt, bt]
