"""eval.scenarios.live：live 域场景（PLAN §3.11 L1-L3；L4 编组缺口待批 2）。"""
from __future__ import annotations

from fastapi.testclient import TestClient

from eval.graders import ProposalGrader, RegexGrader, SimOutcomeGrader, ToolSequenceGrader
from eval.judge import JudgeGrader
from eval.scenario import scenario


def _tick(sess, n: int) -> None:
    for _ in range(n):
        sess.tick()


def _submit(client: TestClient, items: list[dict], name: str = "main") -> None:
    sess = client.app.state.session
    client.post("/api/commands/queue/submit", json={
        "based_on_seq": sess.seq, "name": name, "items": items})


def _built(client: TestClient, type_name: str) -> bool:
    gs = client.app.state.session.world.game_state()
    return any(u.type_name == type_name and u.build_progress >= 1.0
               for u in gs.units)


# ---------------- L1：缺气卡队首 ----------------

def setup_gas_block(client: TestClient) -> None:
    """工厂（要气）卡队首、队列里没有气来源 —— tests/agent/test_round.py 同款。"""
    sess = client.app.state.session
    _tick(sess, 5)
    _submit(client, [
        {"op": "build", "type": "terran/factory",
         "placement": {"kind": "in_region", "region": "home"}},
        {"op": "train", "type": "terran/marine", "count": 6}])
    _tick(sess, 3)


@scenario("L1-gas-block", tags=["live"],
          text="找出最值得修的一件生产问题并提案",
          note="缺气卡队首：期望 observe→propose 插精炼厂，sim 气路解开",
          graders=[
              ToolSequenceGrader(must=["observe", "propose"], forbid=["queue_op", "start_session"]),
              ProposalGrader(expect_op="build", expect_type="terran/refinery"),
              SimOutcomeGrader(final_has={"terran/refinery": 1}),
              RegexGrader(rationale_nonempty=True, reply_not_contains=["done", "完成所有工作"]),
              JudgeGrader(rubric="诊断是否抓住要害（队首等气而队列无气源）；提案是否有依据、"
                                 "行动是否最小而对症；回复是否说清了问题和做法。"
                                 "5=诊断准+提案对症+表达清楚；3=方向对但粗糙；0=没抓住问题或乱动作。"),
          ])
def _l1(client: TestClient) -> None:
    setup_gas_block(client)


# ---------------- L2：浮矿+兵营闲置（产线该动起来） ----------------

def _build(client: TestClient, type_name: str, stable_id: str, cap: int = 400) -> None:
    """提交一座建筑并 tick 到建成（离线世界 1 tick ≈ 1 游戏秒）。"""
    sess = client.app.state.session
    _submit(client, [{"op": "build", "type": stable_id,
                      "placement": {"kind": "in_region", "region": "home"}}])
    for _ in range(cap):
        if _built(client, type_name):
            return
        sess.tick()
    raise RuntimeError(f"预置失败：{stable_id} {cap} tick 内没建成（前置链断了？）")


@scenario("L2-idle-barracks", tags=["live"],
          text="矿在涨但兵营闲着，看看生产上该补什么，提案解决",
          note="浮矿+产能闲置：期望 propose 补机枪兵训练，sim 产线忙起来",
          graders=[
              ToolSequenceGrader(must=["observe", "propose"], forbid=["queue_op"]),
              ProposalGrader(expect_op="train", expect_type="terran/marine"),
              SimOutcomeGrader(final_units={"terran/marine": 1}, horizon=180.0),
              RegexGrader(rationale_nonempty=True),
          ])
def _l2(client: TestClient) -> None:
    sess = client.app.state.session
    _tick(sess, 5)
    # 兵营前置链：depot → barracks（region home 自动找位），队列随后清空、矿在涨
    _build(client, "SUPPLYDEPOT", "terran/supplydepot")
    _build(client, "BARRACKS", "terran/barracks")
    _tick(sess, 10)


# ---------------- L4：编组缺口（current << target） ----------------

@scenario("L4-group-shortfall", tags=["live"],
          text="步兵组兵力远低于编成目标，看看该补什么，提案解决",
          note="编组缺口：默认装配 G_INF marine target=10、场上 0——期望从编组 facts "
               "读出缺口并 propose 补训机枪兵（I17 家族：编组可观测性）",
          graders=[
              ToolSequenceGrader(must=["observe", "propose"], forbid=["queue_op"]),
              ProposalGrader(expect_op="train", expect_type="terran/marine"),
              SimOutcomeGrader(final_units={"terran/marine": 1}, horizon=240.0),
              RegexGrader(rationale_nonempty=True),
          ])
def _l4(client: TestClient) -> None:
    sess = client.app.state.session
    _tick(sess, 5)
    # 前置链备好（depot→barracks），但一个兵都不训 —— 组缺口 0/10 显形
    _build(client, "SUPPLYDEPOT", "terran/supplydepot")
    _build(client, "BARRACKS", "terran/barracks")
    _tick(sess, 10)


# ---------------- L3：快卡人口 ----------------

@scenario("L3-supply-cap", tags=["live"],
          text="看看生产上最该修的一件事，提案解决",
          note="快卡人口：SCV 单堆到供给上限卡队首，期望 propose 补给站，sim 人口不卡",
          graders=[
              ToolSequenceGrader(must=["observe", "propose"], forbid=["queue_op"]),
              ProposalGrader(expect_op="build", expect_type="terran/supplydepot"),
              SimOutcomeGrader(final_has={"terran/supplydepot": 1}),
              RegexGrader(rationale_nonempty=True),
          ])
def _l3(client: TestClient) -> None:
    sess = client.app.state.session
    _tick(sess, 5)
    # SCV 由 CC 产出（开局就有），单堆 12 个把 13 上限吃满 → 队首「供给不足」
    _submit(client, [{"op": "train", "type": "terran/scv", "count": 12}])
    _tick(sess, 3)
