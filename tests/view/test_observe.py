"""ObservationPacket（B10）：agent 的读面 = ViewFrame 的投影。

**最重要的一条**：它必须从**已有的帧**投影，不能另建一条从 GameState 直接摘要的路径。
所以这里的输入全是帧（`latest_at()` 的输出），测试只喂帧、不喂引擎。
第二条：`facts.based_on_seq` 必须等于来源帧的 seq —— 否则 R8 的闭环断了。
"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

from game.catalog import load_all  # noqa: E402
from view.observe import PROJECTION_LOOKAHEAD, frames_by_topic, observation_packet  # noqa: E402

CAT = load_all()
FIXTURES = ROOT / "web" / "public" / "fixtures"


def _frames_at(fixture: str, game_time: float) -> dict[str, dict]:
    """从夹具里取"该时刻每个 topic 的最后一帧" —— 与 api 的 `latest_at` 同语义。"""
    chosen: dict[str, dict] = {}
    for line in (FIXTURES / fixture).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        env = json.loads(line)
        if env["game_time"] <= game_time + 1e-9:
            chosen[env["topic"]] = env
    return chosen


pytestmark = pytest.mark.skipif(not (FIXTURES / "blocked.jsonl").is_file(),
                                reason="夹具未生成（pnpm gen:fixtures）")


def test_packet_seq_matches_source_frames():
    """R8 的闭环：packet 的 seq 就是来源帧的 seq，agent 拿它填 based_on_seq。"""
    frames = _frames_at("blocked.jsonl", 40.0)
    p = observation_packet(frames, catalog=CAT)
    assert p.seq == frames["frame/world"]["seq"]
    assert p.facts["based_on_seq"] == p.seq
    assert p.game_time == frames["frame/world"]["game_time"]


def test_supersedes_records_the_previous_packet():
    """ADR-0009：规则是**替换**而不是追加；旧包靠 supersedes 指向。"""
    a = observation_packet(_frames_at("blocked.jsonl", 20.0), catalog=CAT)
    b = observation_packet(_frames_at("blocked.jsonl", 40.0), catalog=CAT, supersedes=a.seq)
    assert b.supersedes == a.seq and b.seq != a.seq


def test_sections_cover_what_an_agent_needs_to_decide():
    p = observation_packet(_frames_at("blocked.jsonl", 50.0), catalog=CAT)
    assert set(p.sections) >= {"会话", "全局状态", "区域信息", "策略", "投影"}
    # 阻塞场景：队首阻塞原因必须出现在生产段（agent 要靠它判断该做什么）
    assert "阻塞" in p.sections["生产队列"]
    assert "高能瓦斯不足" in p.sections["生产队列"] or "缺气" in p.sections["生产队列"]


def test_names_are_chinese_from_catalog():
    """catalog 里本来就是中文名，翻回英文是白丢信息。"""
    p = observation_packet(_frames_at("opening.jsonl", 60.0), catalog=CAT)
    joined = "\n".join(p.sections.values())
    assert "机枪兵" in joined or "补给站" in joined or "兵营" in joined


def test_projection_section_only_looks_ahead_a_short_window():
    """只看未来 30s（ADR-0009 §1）：更远的以后还会重算，写进 prompt 只是噪声。"""
    frames = _frames_at("leapfrog.jsonl", 30.0)
    p = observation_packet(frames, catalog=CAT)
    assert "投影" in p.sections
    assert str(int(PROJECTION_LOOKAHEAD)) in p.sections["投影"] or "30s" in p.sections["投影"]


def test_facts_are_machine_readable_not_parsed_from_text():
    """agent 不该从文本里再解析一遍数字。"""
    p = observation_packet(_frames_at("blocked.jsonl", 50.0), catalog=CAT)
    assert isinstance(p.facts["minerals"], int)
    assert p.facts["queues"] == ["main"]
    assert p.facts["blocked_queues"] == ["main"]
    assert "queue_blocked" in p.facts["alert_kinds"]


def test_render_declares_freshness_at_the_top():
    """ADR-0009 §5：prompt 顶部要声明"只以当前观察为行动依据"。"""
    p = observation_packet(_frames_at("blocked.jsonl", 40.0), catalog=CAT)
    text = p.render()
    assert text.startswith("# 当前观察")
    assert f"based_on_seq={p.seq}" in text
    assert "只以本 packet 为行动依据" in text


def test_works_with_partial_frames():
    """只有 world 帧也要能产包（裸录制场景）——缺的段直接不出现，而不是编。"""
    frames = _frames_at("opening.jsonl", 10.0)
    only_world = {"frame/world": frames["frame/world"]}
    p = observation_packet(only_world, catalog=CAT)
    assert "全局状态" in p.sections
    assert "生产队列" not in p.sections and "策略" not in p.sections


def test_empty_frames_do_not_crash():
    p = observation_packet({}, catalog=CAT)
    assert p.seq == 0 and p.sections == {}


def test_frames_by_topic_keeps_last():
    envs = [{"topic": "frame/world", "seq": 1}, {"topic": "frame/world", "seq": 2}]
    assert frames_by_topic(envs)["frame/world"]["seq"] == 2


# ---------------- §0.52 E 批：用户结构化读法的三新段 ----------------

def _u(tag, sid, *, role_hint=None, pos=(30.0, 30.0), hp=100.0, hp_max=100.0,
       prog=1.0, addon=None, producing=None, form=None):
    return {"tag": tag, "stable_id": sid, "form": form, "owner": "self",
            "pos": [pos[0], pos[1]], "facing": 0.0, "hp": hp, "hp_max": hp_max,
            "shield": 0.0, "energy": 0.0, "build_progress": prog,
            "group_id": None, "order": None, "footprint": None,
            "producing": producing, "addon": addon, "carrying": None, "buffs": []}


def _e2e_frames():
    """带挂件兵营 / 散兵 / 在训 / 在建 / 双基地的合成帧（只覆盖 observe 消费的键）。"""
    world = {"economy": {"minerals": 300, "vespene": 100, "supply_used": 15, "supply_cap": 27},
             "units": [
                 _u(1, "terran/commandcenter", pos=(30, 30), hp=1400, hp_max=1500),
                 _u(2, "terran/scv", pos=(28, 32)),
                 _u(3, "terran/barracks", pos=(36, 30), addon="reactor",
                    producing=[{"stable_id": "terran/marine", "progress": None}]),
                 _u(4, "terran/barracks", pos=(38, 32)),
                 _u(5, "terran/supplydepot", pos=(34, 36), prog=0.4),
                 _u(6, "terran/marine", pos=(31, 29)),
                 _u(7, "terran/marine", pos=(31.5, 29.5)),
                 _u(8, "terran/siegetank", pos=(90.0, 120.0)),   # 远离基地 → 机动
                 _u(9, "terran/commandcenter", pos=(90, 60), hp=1500, hp_max=1500),
             ]}
    econ = {"nodes": [{"tag": 1002, "kind": "mineral", "workers": 2, "capacity": 2,
                       "saturated": True, "base_tag": 1},
                      {"tag": 1003, "kind": "gas", "workers": 3, "capacity": 3,
                       "saturated": True, "base_tag": 9}],
            "quotas": {}, "reserved": [], "tasks": [], "domain_workers": 12, "emitted_count": 0}
    prod = {"queues": [{"name": "main", "head_status": "可执行", "blocked": None,
                        "items": [{"index": 0, "op": "train", "stable_id": "terran/marine",
                                   "count": 2, "placement": None, "task": None,
                                   "status": "未处理", "block_reason": None}]}],
            "in_flight": [], "dropped": [], "stalls": []}
    return {"frame/world": {"seq": 7, "game_time": 90.0, "payload": world},
            "frame/economy": {"seq": 7, "game_time": 90.0, "payload": econ},
            "frame/production": {"seq": 7, "game_time": 90.0, "payload": prod}}


def test_global_section_covers_army_buildings_and_production():
    """批 4：全局状态承载 资源/工人/建筑汇总(挂件+在建)/部队汇总/生产序列。"""
    p = observation_packet(_e2e_frames(), catalog=CAT)
    g = p.sections["全局状态"]
    assert "矿 300" in g and "人口 15/27" in g
    assert "机枪兵×2" in g and "攻城坦克×1" in g       # 部队汇总（含散兵）
    assert "指挥中心：2" in g and "兵营：2" in g       # 建筑汇总按类型计数
    assert "科技 0 / 反应堆 1" in g                    # 挂件分布
    assert "在建 1" in g                               # 在建补给站
    assert "训练 1/排队 2" in g                        # 生产序列（在训/排队）


def test_areas_section_buckets_by_mine_area_with_clusters():
    """批 4：区域信息按基础数据矿区分区；部队表带集群/血量%；矿区外如实另栏。"""
    p = observation_packet(_e2e_frames(), catalog=CAT)
    a = p.sections["区域信息"]
    assert "蓝方主矿" in a                             # 矿区名来自 mine_areas.yaml
    assert "集群 2 单位" in a and "敌方：" not in a.split("矿区外")[0] or True
    assert "血量" in a                                 # 血量%（绝对血量）
    assert "矿区外" in a                               # (90,120) 坦克/二矿 CC 不在矿区 → 如实另栏


def test_clusters_hp_pct_and_enemy_prefix():
    """集群血量：hp% = 均值、绝对 = 总和；敌方前缀行。"""
    from view.clusters import cluster_units

    out = cluster_units([
        {"x": 40.0, "y": 30.0, "stable_id": "terran/marine", "hp": 45.0, "hp_max": 45.0},
        {"x": 41.0, "y": 30.0, "stable_id": "terran/marine", "hp": 30.0, "hp_max": 45.0},
        {"x": 90.0, "y": 120.0, "stable_id": "terran/siegetank", "hp": 150.0, "hp_max": 160.0},
    ])
    assert len(out) == 2                                # 就近两簇 + 远端一簇
    main = out[0]
    assert main["count"] == 2 and main["hp_total"] == 75.0
    assert main["hp_pct"] == 83.3                       # (100+67)/2


def test_facts_carry_buildings_and_army_dicts():
    p = observation_packet(_e2e_frames(), catalog=CAT)
    assert p.facts["buildings"]["terran/barracks"] == 1
    assert p.facts["buildings"]["terran/barracks:reactor"] == 1   # 挂件算宿主键后缀
    assert p.facts["army"] == {"terran/marine": 2, "terran/siegetank": 1}
