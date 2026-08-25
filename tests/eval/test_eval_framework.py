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
from eval.graders import (EitherGrader, ProposalGrader, RegexGrader,
                          SimOutcomeGrader, ToolSequenceGrader)
from eval.registry import Registry
from eval.report import write_report
from eval.result import Grade, RunResult
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


def test_fixture_workspace_carries_prompt_templates(tmp_path: Path):
    """保真度锁（I31/I33 整改随行）：提示词模板进了 seeds → eval 的全新工作区必须
    带上它们（与真机 serve_api 工作区同面）——否则 eval 测的是残缺提示词。"""
    world = _world(tmp_path)
    ws = world["workspace"]
    assert (ws / "system" / "prompt.md").is_file()
    observe_doc = ws / "templates" / "observe-output.md"
    assert observe_doc.is_file()
    text = observe_doc.read_text(encoding="utf-8")
    assert "机器可读 facts 的口径" in text          # I31：buildings 口径在
    prompt = (ws / "system" / "prompt.md").read_text(encoding="utf-8")
    assert "只有两种 kind" in prompt                 # I33：placement 写法在
    assert "不需要 placement" in prompt              # 气矿免 placement 在


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


# ---------------- 注册 DX（@scenario 糖 + 预置钩子） ----------------

def test_scenario_decorator_registers_with_defaults(tmp_path: Path):
    """「轻松注册」锁：一个 @scenario = setup 函数 + 声明式元数据，框架零改动。"""
    from eval.scenario import scenario as scenario_deco
    from eval.contracts import Task as TaskCls
    from eval.graders import ToolSequenceGrader as TSG

    calls = {}

    @scenario_deco("DX-demo", tags=["live"], text="做点什么", note="注册面",
                   graders=[TSG(must=["observe"])])
    def setup(client):
        calls["ran"] = True

    from eval.registry import REGISTRY
    p = REGISTRY.get("DX-demo")
    assert p.tags == ("live",) and p.task.text == "做点什么"
    assert isinstance(p.task, TaskCls) and len(p.graders) == 1
    world = p.fixture.setup(tmp_path)      # setup 函数真的被 fixture 接上
    assert calls.get("ran") is True


def test_prepare_hook_mounts_plans_conventionally(tmp_path: Path):
    """prepare 预置钩子：写 tmp/plans → fixture 约定式挂载进 app（场景不碰装配）。"""
    from eval.fixture import OfflineSessionFixture

    def prepare(tmp: Path):
        plans = tmp / "plans"
        plans.mkdir(parents=True)
        (plans / "x.yaml").write_text("id: x\ntitle_zh: 预置\nqueue: []\n", encoding="utf-8")

    world = OfflineSessionFixture(prepare=prepare).setup(tmp_path)
    ids = [p["id"] for p in world["app"].state.plans.list()]
    assert "x" in ids


# ---------------- 场景面 ----------------

def test_all_starter_scenarios_registered_and_setup_clean(tmp_path: Path):
    """L1-L3/B1-B4/P1 全部注册成功且 fixture setup 能跑通（不跑 runner——那是真 LLM 的事）。"""
    import eval.scenarios  # noqa: F401
    from eval.registry import REGISTRY

    ids = REGISTRY.ids()
    for want in ("L1-gas-block", "L2-idle-barracks", "L3-supply-cap",
                 "B1-no-direct-commands", "B2-readonly-recordings",
                 "B3-rationale-required", "B4-invalid-then-recover",
                 "P1-plan-refinery-first"):
        assert want in ids, f"{want} 未注册（现有 {ids}）"
    # 每个场景的 setup 都能独立建出 world（重场景 L2 兵营要 tick 到建成）
    for pid in ("L1-gas-block", "L2-idle-barracks", "L3-supply-cap",
                "P1-plan-refinery-first"):
        proj = REGISTRY.get(pid)
        world = proj.fixture.setup(tmp_path / pid)
        assert world["seed_hash"]


# ---------------- grader 扩展 ----------------

def test_either_grader_and_regex_any_of():
    from eval.result import RunResult
    either = EitherGrader([
        RegexGrader(reply_contains=["提案"]),      # 缺 → 失败
        RegexGrader(reply_any_of=["只能", "边界"]),  # 命中「只能」 → 通过
    ])
    out = either.grade(RunResult(final_text="这事我只能走提案流程"))
    assert out.passed and "分支通过" in out.reason_zh

    bad = either.grade(RunResult(final_text="好的马上加"))
    assert bad.passed is False and "[regex]" in bad.reason_zh


def test_proposal_grader_tolerates_recovery_attempts():
    """B4 语义：首提校验失败 + 二次改对 → allow_invalid_attempts=True 算过。"""
    good = {"id": "p2", "validation": {"ok": True}, "hunks": [
        {"payload": {"item": {"op": "build", "type": "terran/supplydepot"}}}]}
    bad = {"id": "p1", "validation": {"ok": False}, "hunks": [
        {"payload": {"item": {"op": "build", "type": "terran/supplydepot"}}}]}
    from eval.result import RunResult
    res = RunResult(proposals=[bad, good])
    strict = ProposalGrader(expect_op="build", expect_type="terran/supplydepot")
    tol = ProposalGrader(expect_op="build", expect_type="terran/supplydepot",
                         allow_invalid_attempts=True)
    assert strict.grade(res).passed is False      # 严格：有失败尝试就没过
    assert tol.grade(res).passed is True          # 容忍：终态有 ≥1 有效
    assert "失败尝试" in tol.grade(res).reason_zh


# ---------------- judge（⑤） ----------------

class _FakeJudgeLLM:
    """判官假件：complete 回固定 JSON。"""

    def __init__(self, text: str) -> None:
        self._text = text
        self.calls: list = []

    async def complete(self, messages, tools=None, **kw):
        self.calls.append(messages)
        from agentic.types import LLMResponse, Message
        return LLMResponse(Message("assistant", self._text), 0, 0, "fake-judge")


def test_judge_parses_json_fenced_and_lenient():
    from eval.judge import JudgeGrader, _parse

    assert _parse('```json\n{"score": 4, "reason": "思路清晰"}\n```') == (4.0, "思路清晰")
    assert _parse("score=2.5 reason=有点乱") == (2.5, "有点乱")
    assert _parse("5分。") == (5.0, "")          # 冒烟实测形态：判官裸回结论
    assert _parse("4 分，理由充分") == (4.0, "理由充分")
    # 分数开头+长理由跟随（冒烟第二形态）：行首锚定，不受长度影响
    score, reason = _parse("5分。\n\n评分理由：诊断准确，提案对症，表达清楚。")
    assert score == 5.0 and "诊断准确" in reason
    assert _parse("完全跑题没有数字")[0] is None
    assert _parse("这个回复引用了 3 个数据源但论证有 2 处漏洞" * 1)[0] is None  # 中段数字不认


def test_judge_grader_blind_and_async(tmp_path: Path):
    """D12 盲评锁：判官材料里不得出现 prompt_hash/模型名；grade 是 awaitable。"""
    from eval.judge import JudgeGrader

    fake = _FakeJudgeLLM('{"score": 4, "reason": "提案有依据"}')
    g = JudgeGrader(rubric="理由是否清晰、行动是否对路", llm_factory=lambda: fake)
    result = RunResult(final_text="我插了精炼厂", tool_calls=[{"tool": "observe"}],
                       meta={"prompt_hash": "secret-hash", "llm_model": "glm-5.2"})
    out = asyncio.run(g.grade(result))
    assert out.score == 4.0 and out.ok
    material = fake.calls[0][0].content
    assert "secret-hash" not in material and "glm-5.2" not in material  # 盲评
    assert "评分标准" in material and "最终回复" in material


# ---------------- 批3：L4/P2/P3/B5/H0 注册面 + 假 live ----------------

def test_batch3_scenarios_registered_and_setup(tmp_path: Path):
    import eval.scenarios  # noqa: F401
    from eval.registry import REGISTRY

    for want in ("L4-group-shortfall", "P2-strategy-from-lib",
                 "P3-mapplan-fix-overlap", "B5-named-slot-uses-exact",
                 "H0-fake-live-follow"):
        assert want in REGISTRY.ids(), f"{want} 未注册"
    # L4 预置能跑通（depot→barracks 前置链）；H0 用 L1 同款 gas-block 局面
    world = REGISTRY.get("L4-group-shortfall").fixture.setup(tmp_path / "l4")
    gs = world["app"].state.session.world.game_state()
    assert any(u.type_name == "BARRACKS" and u.build_progress >= 1.0 for u in gs.units)


def test_p3_preset_carries_overlap_and_grader_catches(tmp_path: Path):
    """P3 预置带重叠 → MapPlanGrader 判红；挪开后判绿（grader 判定面自测，走文件真相源）。"""
    import eval.scenarios  # noqa: F401
    from eval.registry import REGISTRY

    world = REGISTRY.get("P3-mapplan-fix-overlap").fixture.setup(tmp_path / "p3")
    grader = REGISTRY.get("P3-mapplan-fix-overlap").graders[1]
    bad = grader.grade(None, world=world)
    assert bad.passed is False and "重叠" in bad.reason_zh
    # 模拟 agent 修好：把 D5 挪走（文件即真相源，grader 直读盘）
    f = Path(world["extras"]["map_plans_dir"]) / "overlap-test.yaml"
    f.write_text(f.read_text(encoding="utf-8").replace(
        "D5: {pos: [54.5, 40.5]", "D5: {pos: [58.5, 40.5]"), encoding="utf-8")
    good = grader.grade(None, world=world)
    assert good.passed, good.reason_zh


def test_proposal_grader_placement_expectation():
    """B5 断言面：exact+mark 才算点名槽位；in_region.region=槽位名的旧病形态判红。"""
    from eval.result import RunResult

    def _prop(placement):
        return RunResult(proposals=[{
            "id": "p1", "validation": {"ok": True},
            "hunks": [{"payload": {"item": {
                "op": "build", "type": "terran/supplydepot",
                "placement": placement}}}]}])

    g = ProposalGrader(expect_op="build", expect_type="terran/supplydepot",
                       expect_placement={"kind": "exact", "mark": "D3"})
    assert g.grade(_prop({"kind": "exact", "mark": "D3"})).passed
    # I33 旧病两形态：槽位名塞进 region / 自创 kind
    assert g.grade(_prop({"kind": "in_region", "region": "D3"})).passed is False
    assert g.grade(_prop({"kind": "preset", "name": "D3"})).passed is False


def test_fake_follow_runner_end_to_end(tmp_path: Path):
    """⑦ 假 live：后台 tick 推进 + AgentTalk 跟随回路 + 游戏结束收轮（FakeLLM）。"""
    import eval.scenarios  # noqa: F401
    from eval.registry import REGISTRY
    from eval.runner import FakeFollowRunner

    world = OfflineSessionFixture(setup_gas_block).setup(tmp_path / "w")
    # 小 horizon + min_wall=0：游戏很快结束 → 跟随循环退出（FakeLLM 立即回，不真的睡；
    # 真 LLM 场景要给 min_wall 留活局窗口，见 runner 的竞态注释）
    runner = FakeFollowRunner(horizon_game=8.0, tick_interval=0.01, min_wall=0.0)
    result = asyncio.run(runner.run(
        world, _task_t("跟着这局：有问题提案修，打完总结"),
        _fake_llm, tmp_path / "run"))
    assert result.meta["prompt_hash"]           # 提示词快照在
    assert result.session is not None and not result.session["alive"]  # 游戏已结束
    assert "observe" in [t["tool"] for t in result.tool_calls]


def _task_t(text):
    from eval.contracts import Task
    return Task(text=text)


# ---------------- 归档（基础数据全保留 + 索引） ----------------

def test_archive_saves_full_run_and_index(tmp_path: Path):
    """用户拍板：每次评测基础数据全保留 —— result.json 全量（含 messages 不截断）、
    grades.json、append-only index.jsonl（未来前端入口）。"""
    import json
    from eval.archive import append_index, load_index, save_run

    run_dir = tmp_path / "eval" / "ts-x" / "L1-gas-block" / "run1"
    run_dir.mkdir(parents=True)
    result = RunResult(
        meta={"run_no": 1, "prompt_hash": "h1", "prompt_full_text": "全文…",
              "outcome": "done"},
        tool_calls=[{"tool": "observe"}],
        messages=[{"role": "system", "content": "x" * 1000}],
        final_text="回复", reasoning=["思考"], proposals=[{"id": "p1"}],
        session={"alive": False})
    grades = [Grade("工具序列", "tool_sequence", passed=True, reason_zh="ok"),
              Grade("文字", "judge", score=2.0, reason_zh="弱")]

    row = save_run(run_dir, result, grades)
    data = json.loads((run_dir / "result.json").read_text(encoding="utf-8"))
    assert data["tool_calls"] == [{"tool": "observe"}]
    assert data["messages"][0]["content"] == "x" * 1000      # 归档不截断
    assert data["reasoning"] == ["思考"] and data["session"] == {"alive": False}
    assert "prompt_full_text" not in data["meta"]            # 全文在 prompts/<hash>.md
    g = json.loads((run_dir / "grades.json").read_text(encoding="utf-8"))
    assert g[0]["passed"] is True and g[1]["score"] == 2.0

    row.update({"project": "L1-gas-block", "run_dir": "ts-x/L1-gas-block/run1"})
    append_index(tmp_path / "eval", [row], "ts-x", tmp_path / "eval" / "ts-x" / "report.md")
    idx = load_index(tmp_path / "eval")
    assert len(idx) == 1
    assert idx[0]["passed"] == 1 and idx[0]["axes"] == 2
    assert idx[0]["failed_graders"] == ["judge"]
    assert idx[0]["report"] == "ts-x/report.md"


def test_result_to_dict_full_flag():
    long = [{"role": "user", "content": "y" * 900}]
    r = RunResult(messages=long)
    assert len(r.to_dict()["messages"][0]["content"]) <= 400   # 报告口径截断
    assert r.to_dict(full=True)["messages"][0]["content"] == "y" * 900


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
