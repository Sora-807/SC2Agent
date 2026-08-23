"""trace fixture：用用户手动跑的 docs/state_trace.jsonl 验建筑放置 state 机制。
trace 缺失则 skip（需先 uv run python tools/probes/run_recorder.py 生成）。"""
import json
from pathlib import Path

import pytest

TRACE = Path(__file__).resolve().parents[2] / "docs" / "evidence" / "state_trace.jsonl"


def _load():
    if not TRACE.exists():
        pytest.skip(f"trace 不存在：先 uv run python tools/probes/run_recorder.py 生成 {TRACE}")
    return [json.loads(l) for l in TRACE.read_text(encoding="utf-8").splitlines() if l.strip()]


def _unit_type(u: dict) -> str:
    """单位类型名：新 recorder 写 type_name；旧 trace（type_id）兼容读。"""
    return u.get("type_name") or u.get("type_id") or ""


def test_supplydepot_built_to_completion():
    snaps = _load()
    depots = [(s["game_time"], u) for s in snaps for u in s["units"] if _unit_type(u) == "SUPPLYDEPOT"]
    assert depots, "trace 里没 SUPPLYDEPOT"
    assert depots[0][1]["position"] == [45.0, 46.0]
    bps = [u["build_progress"] for _, u in depots]
    assert bps[0] < 0.1, "应从低 build_progress 开始"
    assert 1.0 in bps, "应建到 1.0"


def test_barracks_appears_and_progresses():
    snaps = _load()
    bar = [(s["game_time"], u) for s in snaps for u in s["units"] if _unit_type(u) == "BARRACKS"]
    assert bar, "trace 里没 BARRACKS"
    assert bar[0][1]["position"] == [44.5, 43.5]
    bps = [u["build_progress"] for _, u in bar]
    assert bps[0] < 0.05 and bps[-1] > 0.9  # 0→0.96（120 截断未到 1）


def test_builder_scv_has_building_order():
    snaps = _load()
    found = []
    for s in snaps:
        for u in s["units"]:
            if _unit_type(u) == "SCV":
                for o in u["orders"]:
                    a = o["ability"] or ""
                    if "SupplyDepot" in a or "Barracks" in a:
                        found.append((s["game_time"], a))
    assert found, "没找到建造 SCV 的 build order"
    assert any("SupplyDepot" in a for _, a in found)
    assert any("Barracks" in a for _, a in found)
