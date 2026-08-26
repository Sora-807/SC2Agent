"""api.routes.eval：评测前端面（只读 overview）的装配测。

不跑真评测（那是 tests/eval + CLI 的事）—— 验契约：场景来自 REGISTRY（注册即
出现在前端）、记录来自 index.jsonl（倒序最新在前）、run_howto 带 CLI 用法、
eval_root 可注入隔离（默认与 CLI 的 OUT_ROOT 同一份）。
"""
import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "modules"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from api.app import create_app
from eval.registry import REGISTRY


@pytest.fixture()
def client(tmp_path: Path) -> TestClient:
    # tmp 隔离的 eval_root + 两行记录（追加序=旧→新；路由负责倒序成最新在前）
    (tmp_path / "index.jsonl").write_text(
        "\n".join([
            json.dumps({"ts": "2026-08-25T12:00", "project": "L1-gas-block", "run_no": 1,
                        "outcome": "done", "passed": 1, "axes": 2,
                        "failed_graders": ["ProposalGrader"],
                        "run_dir": "a/L1/run1", "label": "a"}),
            json.dumps({"ts": "2026-08-25T13:00", "project": "L3-supply-cap", "run_no": 2,
                        "outcome": "done", "passed": 4, "axes": 4, "failed_graders": [],
                        "run_dir": "b/L3/run2", "label": "b"}),
        ]) + "\n", encoding="utf-8")
    return TestClient(create_app(eval_root=tmp_path))


def test_overview_scenarios_match_registry(client: TestClient):
    r = client.get("/api/eval/overview")
    assert r.status_code == 200
    body = r.json()
    ids = [s["id"] for s in body["scenarios"]]
    assert set(ids) == set(REGISTRY.ids())    # 注册即出现（前端不用另登记）
    s0 = body["scenarios"][0]
    assert {"id", "tags", "runs", "judge_model", "graders"} <= set(s0)


def test_overview_runs_latest_first_and_howto(client: TestClient):
    body = client.get("/api/eval/overview").json()
    runs = body["runs"]
    assert [r["project"] for r in runs] == ["L3-supply-cap", "L1-gas-block"]  # 倒序
    assert runs[1]["failed_graders"] == ["ProposalGrader"]                   # 原样带失败项
    assert "eval.run" in body["run_howto"]                                   # 跑法提示在


# ---------------- 项目详情钻取（PLAN-EVAL-FRONTEND 批 A，introspection 只读） ----------------

def test_project_detail_returns_four_contracts(client: TestClient):
    r = client.get("/api/eval/projects/L1-gas-block")
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == "L1-gas-block"
    assert body["task"]["text"]                                  # 任务原话在
    assert {"class", "module", "params"} <= set(body["fixture"])
    assert body["fixture"]["params"]["setup_fn"].startswith(
        "<_l1> @ eval/scenarios/live.py:")                        # 函数→定位标注（EF2）
    assert any(g["class"] == "ToolSequenceGrader"
               and g["params"]["must"] == ["observe", "propose"]
               for g in body["graders"])                         # 构造参数可见


def test_project_detail_404_lists_available_ids(client: TestClient):
    r = client.get("/api/eval/projects/no-such")
    assert r.status_code == 404
    assert "L1-gas-block" in r.json()["detail"]                   # 可用清单在（I39 口径）


# ---------------- 单 run 指标钻取（PLAN-EVAL-FRONTEND 批 B，直读归档） ----------------

@pytest.fixture()
def run_client(tmp_path: Path) -> TestClient:
    """手造归档：一个正常 run（含批次 prompts 快照 + index 行，批次名带 `+`）
    + 一个孤儿 run（盘上有 result.json 但 index 没行 = 被中断的批次）。"""
    batch = tmp_path / "20260826-000000-T1+T2"
    run = batch / "T1-demo" / "run1"
    run.mkdir(parents=True)
    (run / "result.json").write_text(json.dumps({
        "meta": {"run_no": 1, "llm_model": "fake", "outcome": "done", "duration_s": 1.0,
                 "prompt_hash": "abc123", "seed_hash": "s1", "input_tokens": 10,
                 "output_tokens": 5, "reasoning_clipped": 0},
        "tool_calls": [{"tool": "observe", "duration_ms": 12, "result_preview": "x"}],
        "final_text": "回复全文", "reasoning": [], "segments": [], "proposals": [],
        "changes": [{"area": "live", "action": "edit", "ref": "加一个", "label": "加一个",
                     "target": "#/production"}],
        "workspace": {"session/current.md": 100}, "session": {"state": "对局中"},
        "messages": [{"role": "system", "content": "长" * 1000},
                     {"role": "user", "content": "问"}],
    }, ensure_ascii=False), encoding="utf-8")
    (run / "grades.json").write_text(json.dumps(
        [{"axis": "工具序列", "grader": "tool_sequence", "passed": True, "score": None,
          "reason_zh": "序列符合要求"}], ensure_ascii=False), encoding="utf-8")
    (batch / "prompts").mkdir(parents=True)
    (batch / "prompts" / "abc123.md").write_text("# 提示词全文", encoding="utf-8")
    (tmp_path / "index.jsonl").write_text(json.dumps({
        "ts": "2026-08-26T00:00", "project": "T1-demo", "run_no": 1, "outcome": "done",
        "passed": 1, "axes": 1, "failed_graders": [], "label": "T1+T2",
        "run_dir": "20260826-000000-T1+T2/T1-demo/run1", "report": "report.md"}) + "\n",
        encoding="utf-8")
    orphan = tmp_path / "20260826-000001-T3" / "T3-orphan" / "run1"
    orphan.mkdir(parents=True)
    (orphan / "result.json").write_text(json.dumps(
        {"meta": {}, "tool_calls": [], "final_text": "", "reasoning": [], "segments": [],
         "proposals": [], "changes": [], "workspace": {}, "session": None, "messages": []}),
        encoding="utf-8")
    (orphan / "grades.json").write_text("[]", encoding="utf-8")
    return TestClient(create_app(eval_root=tmp_path))


def test_run_detail_strips_messages_and_merges_index(run_client: TestClient):
    r = run_client.get("/api/eval/runs/20260826-000000-T1+T2/T1-demo/run1")   # + 在 path 是字面量
    assert r.status_code == 200
    body = r.json()
    assert body["messages"] == []                       # 默认剥掉（EF4）
    assert body["messages_count"] == 2
    assert body["final_text"] == "回复全文"
    assert body["grades"][0]["grader"] == "tool_sequence"
    assert body["index_row"]["label"] == "T1+T2"        # index 上下文合并进来


def test_run_detail_messages_summary_opt_in(run_client: TestClient):
    body = run_client.get("/api/eval/runs/20260826-000000-T1+T2/T1-demo/run1?messages=1").json()
    assert len(body["messages"][0]["content"]) == 400   # 400 字/条摘要（同报告瘦身）


def test_run_detail_orphan_has_no_index_row(run_client: TestClient):
    body = run_client.get("/api/eval/runs/20260826-000001-T3/T3-orphan/run1").json()
    assert body["index_row"] is None                    # 中断批次：盘上有、账上没有


def test_run_detail_error_semantics_split(run_client: TestClient):
    # `..` 会被规范化的客户端（httpx/浏览器）在发送前就折掉，所以用绝对路径形态
    # 测越界分支（守卫对不规范的调用方仍然成立）
    assert run_client.get("/api/eval/runs/C:/evil").status_code == 400      # 越界
    assert run_client.get("/api/eval/runs/no-such-dir").status_code == 404  # 不存在


def test_run_prompt_served_and_missing(run_client: TestClient):
    r = run_client.get("/api/eval/runs/20260826-000000-T1+T2/T1-demo/run1/prompt")
    assert r.status_code == 200
    assert r.text == "# 提示词全文"
    assert r.headers["content-type"].startswith("text/plain")
    # 孤儿 run（中断批次没写 prompts/）→ 404 带说明，不是 500
    r2 = run_client.get("/api/eval/runs/20260826-000001-T3/T3-orphan/run1/prompt")
    assert r2.status_code == 404
    assert "快照" in r2.json()["detail"]


# ---------------- POST 触发式跑批（2026-08-26 前端操作面） ----------------

def _wait_job(client: TestClient, timeout: float = 90.0) -> dict:
    import time as _t

    deadline = _t.time() + timeout
    while _t.time() < deadline:
        job = client.get("/api/eval/overview").json().get("job")
        if job and job.get("state") != "running":
            return job
        _t.sleep(0.3)
    pytest.fail("job 未在时限内结束")


def test_post_run_end_to_end_with_fake_llm(tmp_path: Path):
    """POST → 后台线程跑（fake LLM）→ job done + 记录落 index.jsonl（前端轮询同一份）。"""
    from agentic.llm import FakeLLMClient
    from agentic.types import LLMResponse, Message, ToolCall

    def factory():
        return FakeLLMClient([
            LLMResponse(Message("assistant", None, [ToolCall("c1", "observe", {})]), 0, 0, "fake"),
            LLMResponse(Message("assistant", "看到了，当前局面正常。"), 0, 0, "fake"),
        ])

    c = TestClient(create_app(eval_root=tmp_path, eval_llm_factory=factory))
    r = c.post("/api/eval/run", json={"ids": ["L1-gas-block"], "runs": 1})
    assert r.status_code == 200 and r.json()["job"]["state"] == "running"
    job = _wait_job(c)
    assert job["state"] == "done", job.get("error")
    assert job["report"]
    # 记录真的进了 index（overview 的 runs 出现该场景）
    body = c.get("/api/eval/overview").json()
    assert any(x["project"] == "L1-gas-block" for x in body["runs"])


def test_post_run_rejects_concurrent_and_bad_ids(tmp_path: Path):
    from api.routes import eval as eval_route

    c = TestClient(create_app(eval_root=tmp_path))
    # 坏 id：400 带清单（不 500）
    r = c.post("/api/eval/run", json={"ids": ["no-such"]})
    assert r.status_code == 400
    # 并发锁：伪造一个 running job 占位
    eval_route._JOB["cur"] = {"state": "running", "label": "x"}
    try:
        assert c.post("/api/eval/run", json={}).status_code == 409
    finally:
        eval_route._JOB["cur"] = None
