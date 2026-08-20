"""离线 build-order 仿真器 CLI（planner.P4）。

吃一个 production_sequence（yaml 或内置模块名）+ 起始状态（CC + N SCV + 矿/气）→
逐秒仿真 → 打印资源/单位曲线 + 摘要（凑齐时间/峰值余矿/卡点/完成事件）。

用法：
  uv run python run_sim.py                       # 内置示例：factory_chain（4 坦克）
  uv run python run_sim.py docs/buildorder.yaml  # 自定义 production_sequence
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "modules"))

import yaml  # noqa: E402

from game import GameState, Grid, Order, Owner, Point2, Unit  # noqa: E402
from game.catalog import load_terran  # noqa: E402
from planner import Planner, ProductionModuleInstance  # noqa: E402


def make_starting_state(scv_count=12, minerals=50, gas=0, supply_cap=15):
    """标准开局 GameState 快照：CC + N SCV 采矿物 + 起始资源。"""
    cc = _u(1, "COMMANDCENTER")
    patch = _u(900, "MINERALFIELD", owner=Owner.NEUTRAL)
    scvs = [_u(100 + i, "SCV", orders=[Order(ability="Gather", target_tag=900)])
            for i in range(scv_count)]
    g = Grid(1, 1, [[0]])
    return GameState(seq=0, game_time=0.0, minerals=minerals, vespene=gas,
                     supply_used=scv_count, supply_cap=supply_cap,
                     units=[cc] + scvs, map_size=(176, 160), creep=g, visibility=g,
                     resources=[patch])


def _u(tag, type_name, owner=Owner.SELF, orders=()):
    return Unit(tag=tag, type_name=type_name, position=Point2(0, 0), owner=owner,
                hp=1.0, hp_max=1.0, shield=0.0, energy=0.0, build_progress=1.0,
                orders=list(orders))


def load_seq(path: str | None) -> list[ProductionModuleInstance]:
    if path is None:
        return [ProductionModuleInstance("m0", "factory_chain", params={"tank_count": 4})]
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return [ProductionModuleInstance(
        instance_id=m["instance_id"], module_ref=m["module_ref"],
        version=m.get("version", 1), params=m.get("params", {}),
    ) for m in data.get("production_sequence", [])]


def main() -> None:
    seq_path = sys.argv[1] if len(sys.argv) > 1 else None
    seq = load_seq(seq_path)
    cat = load_terran()
    gs = make_starting_state()
    planner = Planner(cat)
    until = 300
    curve = planner.project(gs, seq, until=until)

    print(f"=== 仿真 {len(curve.points)} 秒（until={until}）===")
    print(f"峰值余矿: {curve.peak_minerals():.0f}")
    completed = [(e.t, e.type) for e in curve.events if e.kind == "completed"]
    print("完成事件:")
    for t, ty in completed:
        print(f"  t={t:.0f}  {ty}")
    stalls = curve.stalls()
    if stalls:
        print(f"卡点（{len(stalls)}）:")
        for e in stalls:
            print(f"  t={e.t:.0f}  {e.type}  {e.reason}")
    else:
        print("无卡点")
    print("--- 曲线（每 10 秒）---")
    for p in curve.points:
        if p.t % 10 == 0:
            print(f"  t={p.t:.0f}  矿={p.minerals:.0f}  气={p.gas:.0f}  "
                  f"supply={p.supply_used}/{p.supply_cap}  "
                  f"buildings={p.buildings}  units={p.units}  在途={p.in_flight_count}")


if __name__ == "__main__":
    main()
