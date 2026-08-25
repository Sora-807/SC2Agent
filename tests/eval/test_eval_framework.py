"""eval 框架装配测：FakeLLMClient 脚本跑通轻管线全链（fixture→runner→提取→grader→报告）。

不验 LLM 判断力（那是 CLI 真 LLM 的事）——验的是 PLAN-AGENT-EVAL 的契约：
RunResult 提取齐、提示词快照真（D16）、四 grader 判定正确、报告落盘、注册闭集。
"""
import asyncio
from pathlib import Path

import pytest
from agentic.llm import FakeLLMClient
from agentic.types import LLMResponse, Message, ToolCall

from eval.contracts import Project, Task
from eval.fixture import OfflineSessionFixture
from eval.graders import ProposalGrader, RegexGrader, SimOutcomeGrader, ToolSequenceGrader
from eval.registry import Registry
from eval.report import write_report
from eval.result import Grade
from eval.runner import SingleRoundRunner
from eval.scenarios.live import setup_gas_block

PROPOSAL_ARGS = {
    "kind": "production_queue",
    "title_zh": "补精炼厂解开缺气的队首",
    "rationale_zh": "队首在等气而队列里没有气的来源，先插精炼厂，否则整队一直冻结。",
    "target": {"queue": "main"},
    "hunks": [{"id": "h1", "kind": "insert", "text_zh": "队首插精炼厂",
               "payload": {"before_uid": "q01",
                           "item": {"op": "build", "type": "terran/refinery", "count": 1}}}],
}


def _fake_llm() -> FakeLLMClient:
    return FakeLLMClient([
        LLMResponse(Message("assistant", None, [ToolCall("c1", "observe", {})]), 0, 0, "fake"),
        LLMResponse(Message("assistant", None, [ToolCall("c2", "propose", PROPOSAL_ARGS)]), 0, 0, "fake"),
        LLMResponse(Message("assistant", "队首在等气，我插了一座精炼厂解开。"), 0, 0, "fake"),
    ])


def _world(tmp: Path) -> dict:
    return OfflineSessionFixture(setup_gas_block).setup(tmp)


# ---------------- 注册（D5） ----------------

def test_registry_rejects_duplicate_ids_and_open_set_tags():
    reg = Registry()
    p = Project(id="X", tags=("live",), fixture=None, task=Task("t"), runner=None, graders=[])
    reg.add(p)
    with pytest.raises(ValueError, match="重复"):
        reg.add(Project(id="X", tags=("live",), fixture=None, task=Task("t"), runner=None, graders=[]))
    with pytest.raises(ValueError, match="闭集"):
        reg.add(Project(id="Y", tags=("非标签",), fixture=None, task=Task("t"), runner=None, graders=[]))
    assert [x.id for x in reg.select(tags=["live"])] == ["X"]


# ---------------- fixture ----------------

def test_offline_fixture_builds_seeded_world(tmp_path: Path):
    world = _world(tmp_path)
    assert world["client"].get("/api/session").status_code == 200
    assert (world["workspace"] / "system").is_dir() or any(
        world["workspace"].iterdir())   # 种子工作区非空（memory_seed 只补缺失）
    assert world["seed_hash"]          # D16：种子指纹在 run 前定死


# ---------------- 端到端（FakeLLM） ----------------

def test_single_round_end_to_end(tmp_path: Path):
    world = _world(tmp_path)
    runner = SingleRoundRunner()
    result = asyncio.run(runner.run(
        world, Task("找出最值得修的一件生产问题并提案"),
        _fake_llm, tmp_path / "run"))

    # RunResult 提取（trace 地基）
    assert [t["tool"] for t in result.tool_calls] == ["observe", "propose"]
    assert result.proposals and result.proposals[0]["validation"]["ok"]
    assert result.final_text.startswith("队首在等气")
    assert result.changes or result.proposals   # D9：轮内 ChangeLog 或持久侧至少一边有货
    # D16：提示词快照 = LLM 真看到的组装全文（含任务段与工具清单）
    assert "# 任务" in result.meta["prompt_full_text"]
    assert "# 可用工具" in result.meta["prompt_full_text"]
    assert result.meta["prompt_hash"] and result.meta["seed_hash"]

    # 四 grader 判定
    world_again = {"client": world["client"]}
    grades = [
        ToolSequenceGrader(must=["observe", "propose"], forbid=["queue_op"]),
        ProposalGrader(expect_op="build", expect_type="terran/refinery"),
        SimOutcomeGrader(final_has={"terran/refinery": 1}),
        RegexGrader(rationale_nonempty=True),
    ]
    outs = [g.grade(result, world=world_again) for g in grades]
    assert all(g.passed for g in outs), [g.reason_zh for g in outs]


def test_graders_fail_loudly_on_missing(tmp_path: Path):
    """grader 的判定面：没提案/没工具调用时报得出原因，不是静默过。"""
    from eval.result import RunResult
    empty = RunResult()
    g1 = ToolSequenceGrader(must=["observe"]).grade(empty)
    assert g1.passed is False and "没调 observe" in g1.reason_zh
    g2 = ProposalGrader(expect_type="terran/refinery").grade(empty)
    assert g2.passed is False and "没有提案" in g2.reason_zh
    g3 = RegexGrader(rationale_nonempty=True).grade(empty)
    assert g3.passed is False


def test_grade_ok_semantics():
    assert Grade("文字", "j", passed=True).ok
    assert not Grade("文字", "j", passed=False).ok
    assert Grade("文字", "j", score=4.0).ok          # LLM 轴 >=3 算过
    assert not Grade("文字", "j", score=2.0).ok
    assert not Grade("文字", "j").ok                 # 两者都空 = 没判成，不算过


# ---------------- 报告（D6/D16） ----------------

def test_report_written_with_prompt_snapshot(tmp_path: Path):
    world = _world(tmp_path / "w")
    runner = SingleRoundRunner()
    result = asyncio.run(runner.run(
        world, Task("t", note="缺气卡队首"),
        _fake_llm, tmp_path / "run"))
    grades = [ToolSequenceGrader(must=["observe", "propose"]).grade(result)]
    path = write_report(tmp_path / "out", "测试", [
        {"project": "L1-gas-block", "task": "缺气卡队首",
         "result": result, "grades": grades}])
    text = path.read_text(encoding="utf-8")
    assert "L1-gas-block" in text and "工具序列" in text
    assert result.meta["prompt_hash"] in text
    snap = tmp_path / "out" / "prompts" / f"{result.meta['prompt_hash']}.md"
    assert snap.is_file() and "# 可用工具" in snap.read_text(encoding="utf-8")
