"""run_*.py 真机脚本的离线守卫（T6 前置 / H5）。

真机脚本不进 pytest（需要 SC2），于是最容易漂移：a41abc9 把 ProductionRuntime._build_flight 改名成
_build_flights，run_full_flow.py 的 tick 日志就一直是坏的 —— 首个日志帧 AttributeError，
而 T6 恰恰要用它做冒烟。这里补两层离线守卫：
1. 脚本里的内联 strategy/assembly 必须能编译 + 构造引擎（词表/命名参数/catalog 迁移不漏脚本）。
2. 脚本引用的引擎/运行时私有属性必须真实存在（改名即测试红）。
"""
import importlib
import re
import sys
from pathlib import Path

from driver.fake import FakeGamePort
from flow.engine import FlowEngine
from flow.manifest import parse_assembly, parse_strategy
from game.catalog import load_all
from production.runtime import ProductionRuntime

ROOT = Path(__file__).resolve().parents[2]
CAT = load_all()


class _NullPort:
    def submit_operations(self, ops):
        pass


def _import(name: str):
    # 真机脚本住在 tools/probes/（2026-08-23 根目录清理迁入）；离线守卫语义不变
    probes = str(ROOT / "tools" / "probes")
    if probes not in sys.path:
        sys.path.insert(0, probes)
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    return importlib.import_module(name)


def test_inline_strategies_compile_and_engine_constructs():
    """run_flow_arrived / run_flow_slice / run_full_flow 的内联策略离线编译 + 构造引擎。"""
    cases = [
        ("run_flow_arrived", lambda m: (m.STRATEGY, m.ASSEMBLY)),
        ("run_flow_slice", lambda m: (m.STRATEGY, m.ASSEMBLY)),
        ("run_full_flow", lambda m: (m.STRATEGY, m._assembly([50.0, 50.0]))),
    ]
    for name, pick in cases:
        mod = _import(name)
        strategy_yaml, assembly_yaml = pick(mod)
        st = parse_strategy(strategy_yaml)
        asm = parse_assembly(assembly_yaml)
        eng = FlowEngine(st, asm, FakeGamePort(script=[]), catalog=CAT)
        assert eng._active_step == st.initial_step, name


def test_tank_push_script_uses_repo_sample():
    """run_tank_marine_push 直接吃 docs/tank_marine_push.yaml（含 definitions），必须能编译。"""
    mod = _import("run_tank_marine_push")
    st = parse_strategy(mod._strategy_yaml())
    assert st.id == "tank_marine_push"
    assert "front_point" in st.definitions and "formed" in st.definitions


def test_run_scripts_only_touch_existing_runtime_attrs():
    """脚本引用的 ProductionRuntime 私有属性必须存在（防 _build_flight → _build_flights 这类漂移）。"""
    rt = ProductionRuntime(CAT, _NullPort())
    pattern = re.compile(r"_runtime\.(_[a-z_]+)")
    for script in sorted(ROOT.glob("run_*.py")):
        for attr in sorted(set(pattern.findall(script.read_text(encoding="utf-8")))):
            assert hasattr(rt, attr), f"{script.name} 引用了 ProductionRuntime 上不存在的 {attr!r}"


def test_run_scripts_only_touch_existing_engine_attrs():
    """同上，针对 FlowEngine（_active_step/_done/_bindings/_alloc 这些真机日志常用字段）。"""
    st = parse_strategy(_import("run_flow_slice").STRATEGY)
    asm = parse_assembly(_import("run_flow_slice").ASSEMBLY)
    eng = FlowEngine(st, asm, FakeGamePort(script=[]), catalog=CAT)
    pattern = re.compile(r"_engine\.(_[a-z_]+)")
    for script in sorted(ROOT.glob("run_*.py")):
        for attr in sorted(set(pattern.findall(script.read_text(encoding="utf-8")))):
            assert hasattr(eng, attr), f"{script.name} 引用了 FlowEngine 上不存在的 {attr!r}"



def test_tank_script_shares_one_ownership_table():
    """ADR-0030 会话装配自检：Allocator / 生产 / 维持器必须共用同一份 reservations 与同一个 Allocator。

    真机脚本不进 pytest，装配写错（各自 new 一份）在离线是看不出来的 —— 这条就是那个自检。
    """
    import inspect

    src = inspect.getsource(_import("run_tank_marine_push"))
    assert "reservations=self._reservations" in src, "生产/维持器要共用征用登记"
    assert "pool=self._alloc" in src, "维持器领地要来自同一个 Allocator（WorkerPoolPort）"
    assert "allocator=self._alloc" in src, "FlowEngine 要注入同一个 Allocator"
    assert "self._economy.on_game_state" in src, "维持器必须每帧 tick，否则不收敛"
    assert 'submit_queue("steward"' not in src, "手写的采矿维持循环应已删除（ADR-0030 验收 9）"



def test_target_semantics_scripts_do_not_derive_count_from_live_counts():
    """陷阱守卫：一旦脚本装配了 EconomyKeeper，assign_workers 就是**目标值**（ADR-0030 D2），

    此时再写 `count=len(idle)` 这种"按当前空闲数派人"的老写法，会把目标写成一个随帧抖动的数字。
    未装配维持器的老脚本（run_full_flow 等）仍是一次性语义，可以继续用老写法 —— 但两者不能混。
    这条守卫让"给老脚本接上维持器却忘了改 count"变成测试红，而不是真机上气工数量乱跳。
    """
    for script in sorted(ROOT.glob("run_*.py")):
        src = script.read_text(encoding="utf-8")
        if "economy=" not in src:
            continue  # 老脚本：一次性语义，不受这条约束
        offenders = [line.strip() for line in src.splitlines()
                     if "assign_workers" in line and "count=len(" in line]
        assert not offenders, (
            f"{script.name} 装配了 EconomyKeeper（目标值语义），但仍按当前数量派人：{offenders}"
        )
