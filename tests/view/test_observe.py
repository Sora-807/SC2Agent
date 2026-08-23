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
    assert set(p.sections) >= {"会话", "经济", "生产", "策略", "投影"}
    # 阻塞场景：队首阻塞原因必须出现在生产段（agent 要靠它判断该做什么）
    assert "阻塞" in p.sections["生产"]
    assert "高能瓦斯不足" in p.sections["生产"] or "缺气" in p.sections["生产"]


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
    assert "经济" in p.sections
    assert "生产" not in p.sections and "策略" not in p.sections


def test_empty_frames_do_not_crash():
    p = observation_packet({}, catalog=CAT)
    assert p.seq == 0 and p.sections == {}


def test_frames_by_topic_keeps_last():
    envs = [{"topic": "frame/world", "seq": 1}, {"topic": "frame/world", "seq": 2}]
    assert frames_by_topic(envs)["frame/world"]["seq"] == 2
