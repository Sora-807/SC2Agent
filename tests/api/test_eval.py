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
