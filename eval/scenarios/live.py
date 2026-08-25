"""eval.scenarios.live：live 域场景（PLAN §3.11 L1-L4）。v1 先落 L1。"""
from __future__ import annotations

from fastapi.testclient import TestClient

from eval.contracts import Project, Task
from eval.fixture import OfflineSessionFixture
from eval.graders import ProposalGrader, RegexGrader, SimOutcomeGrader, ToolSequenceGrader
from eval.registry import register
from eval.runner import SingleRoundRunner


def setup_gas_block(client: TestClient) -> None:
    """L1 局面：工厂（要气）卡队首、队列里没有气来源 —— tests/agent/test_round.py 同款。"""
    sess = client.app.state.session
    for _ in range(5):
        sess.tick()
    client.post("/api/commands/queue/submit", json={
        "based_on_seq": sess.seq, "name": "main", "items": [
            {"op": "build", "type": "terran/factory",
             "placement": {"kind": "in_region", "region": "home"}},
            {"op": "train", "type": "terran/marine", "count": 6}]})
    for _ in range(3):
        sess.tick()


_L1 = register(Project(
    id="L1-gas-block",
    tags=("live", "轻"),
    fixture=OfflineSessionFixture(setup_gas_block),
    task=Task(
        text="找出最值得修的一件生产问题并提案",
        note="缺气卡队首：期望 observe→propose 插精炼厂，sim 气路解开",
    ),
    runner=SingleRoundRunner(),
    graders=[
        ToolSequenceGrader(must=["observe", "propose"], forbid=["queue_op", "start_session"]),
        ProposalGrader(expect_op="build", expect_type="terran/refinery"),
        SimOutcomeGrader(final_has={"terran/refinery": 1}),
        RegexGrader(rationale_nonempty=True, reply_not_contains=["done", "完成所有工作"]),
    ],
))
