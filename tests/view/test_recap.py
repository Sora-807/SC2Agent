"""view.recap：录像衍生摘要渲染器（I20 回归锁）。

锁三件事：① 时间线提取对（建筑落成/人口上限/警报去重/策略转移）；
② 终局盘点读的是**最后一帧**；③ 消失建筑有痕迹。这些是 Agent 复盘上一局时
读到的全部事实 —— 渲染错 = agent 学错。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "modules"))

from view.recap import render_recording_summary, render_recordings_index


def _world(t, units, minerals=50, gas=0, used=8, cap=13):
    return {"topic": "frame/world", "game_time": t, "payload": {
        "economy": {"minerals": minerals, "vespene": gas,
                    "supply_used": used, "supply_cap": cap},
        "units": units}}


def _bld(tag, sid, prog=1.0):
    return {"tag": tag, "stable_id": sid, "owner": "self", "build_progress": prog,
            "footprint": {"tl": [0, 0], "br": [2, 2]}}


def _unit(tag, sid, owner="self"):
    return {"tag": tag, "stable_id": sid, "owner": owner, "build_progress": 1.0,
            "footprint": None}


def test_summary_covers_headline_timeline_and_final_state():
    frames = [
        {"topic": "frame/session", "game_time": 0.0, "payload": {
            "state": "对局中", "map_name": "LadderMap", "my_race": "terran",
            "enemy_race": "protoss"}},
        _world(0.0, [_unit(1, "terran/scv")] * 8 + [_bld(10, "terran/commandcenter")]),
        _world(20.0, [_bld(11, "terran/supplydepot", 0.4)]),
        _world(30.0, [_bld(11, "terran/supplydepot", 1.0)],
               minerals=100, used=10, cap=21),
        {"topic": "frame/alerts", "game_time": 35.0, "payload": {"alerts": [
            {"kind": "gas_float", "severity": "warn", "text_zh": "瓦斯没花掉"}]}},
        _world(60.0, [_bld(11, "terran/supplydepot"), _unit(20, "terran/marine")] * 3,
               minerals=290, used=13, cap=21),
    ]
    text = render_recording_summary(frames)
    assert "LadderMap" in text and "时长 60 秒" in text
    assert "落成 terran/supplydepot" in text
    assert "人口上限 13→21" in text
    assert "[warn] 瓦斯没花掉" in text
    assert "terran/marine ×3" in text          # 终局部队按最后一帧盘点
    assert "矿 290" in text


def test_lost_building_leaves_a_trace():
    frames = [
        _world(0.0, [_bld(10, "terran/barracks")]),
        _world(10.0, [_bld(10, "terran/barracks")]),
        _world(100.0, []),                       # 10s 之后兵营再没出现
    ]
    text = render_recording_summary(frames)
    assert "terran/barracks 在 t=10s 后未再出现" in text


def test_alert_dedupe_collapses_repeats():
    frames = [
        {"topic": "frame/alerts", "game_time": t, "payload": {"alerts": [
            {"kind": "mineral_float", "severity": "info", "text_zh": "晶体矿没花掉"}]}}
        for t in (10.0, 11.0, 12.0, 80.0)      # 三连击 + 一个窗口外的复发
    ]
    text = render_recording_summary(frames)
    assert text.count("晶体矿没花掉") == 2       # 10s 首次 + 80s 复发；中间两条压掉


def test_strategy_transitions_and_exit_from_last_flow_frame():
    frames = [
        {"topic": "frame/flow", "game_time": 1.0, "payload": {"strategies": [{
            "strategy_ref": "tank_push", "transitions": [
                {"from_step": "formup", "to": "advance", "kind": "branch", "reason": "集结完成",
                 "at": 40.0}],
            "exit_record": None, "done": False}]}},
        {"topic": "frame/flow", "game_time": 90.0, "payload": {"strategies": [{
            "strategy_ref": "tank_push", "transitions": [
                {"from_step": "formup", "to": "advance", "kind": "branch", "reason": "集结完成",
                 "at": 40.0}],
            "exit_record": {"kind": "victory", "reason": "敌主基地摧毁", "at": 88.0},
            "done": True}]}},
    ]
    text = render_recording_summary(frames)
    assert "策略 formup→advance（branch/集结完成）" in text
    assert "victory/敌主基地摧毁" in text


def test_index_renders_table_from_metas():
    idx = render_recordings_index([
        {"id": "rec-a", "my_race_zh": "人族", "enemy_race_zh": "神族",
         "map": "LadderMap", "to": 143.0, "envelopes": 818, "state": "已结束"},
    ])
    assert "rec-a.md" in idx and "人族 vs 神族" in idx and "143s" in idx
    assert render_recordings_index([]).count("还没有录像") == 1
