"""评测管线的前端面：场景注册表 + 运行记录 + 触发式跑批（2026-08-26 起）。

跑批两条入口同一份归档：CLI（`uv run python -m eval.run`）与 POST /api/eval/run
（前端评测页按钮）。API 版在后台线程跑（分钟级：LLM 依赖 + 每场景 N 次），
job 状态挂在 app.state.eval_job，前端轮询 overview 里的 job 字段看进度。

import 全部延迟到函数体：eval 包顶层（fixture）import api.app，模块级 import
会成环（routes → eval.run → scenarios → fixture → api.app 半初始化）。
"""
from __future__ import annotations

import asyncio
import threading
import time

from fastapi import APIRouter, HTTPException, Request

router = APIRouter()

#: CLI 用法（前端展示 + 不忘自己）
RUN_HOWTO = "CLI：uv run python -m eval.run [场景id] [--runs N]；或本页「跑评测」按钮（同一份归档）"


def _job_dict() -> dict | None:
    """当前 job 状态（无 job = None）。模块级单例：单进程 uvicorn 下即够。"""
    return _JOB["cur"]


_JOB: dict = {"cur": None}


@router.get("/api/eval/overview")
def eval_overview(request: Request) -> dict:
    """场景清单 + 运行记录（index.jsonl 原样行，倒序最新在前）+ 当前 job 状态。"""
    from eval.archive import load_index
    from eval.registry import REGISTRY
    from eval.run import OUT_ROOT

    root = getattr(request.app.state, "eval_root", None) or OUT_ROOT
    scenarios = [
        {"id": p.id, "tags": list(p.tags), "runs": p.runs,
         "judge_model": p.judge_model,
         "graders": [type(g).__name__ for g in p.graders]}
        for p in REGISTRY.select()
    ]
    runs = load_index(root)
    runs.reverse()   # 最新在前
    return {"scenarios": scenarios, "runs": runs, "run_howto": RUN_HOWTO,
            "eval_root": str(root), "job": _job_dict()}


@router.get("/api/eval/projects/{project_id}")
def eval_project(project_id: str) -> dict:
    """单项目完整构成（PLAN-EVAL-FRONTEND 批 A：类数据 introspection，只读）。

    404 带可用 id 清单（I39 口径：列出来别让人猜）。
    """
    from eval.describe import describe_project
    from eval.registry import REGISTRY

    try:
        p = REGISTRY.get(project_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
    return describe_project(p)


@router.post("/api/eval/run")
def eval_run(body: dict, request: Request) -> dict:
    """触发跑批（后台线程）。body：{ids?: [场景id]，runs?: N}（缺省全部 × 3）。

    一次只允许一个 job（409）；LLM 未配 .env 时首跑报错进 job.error（不 500）。
    """
    from eval.registry import REGISTRY

    if _JOB["cur"] is not None and _JOB["cur"].get("state") == "running":
        raise HTTPException(status_code=409, detail="已有跑批在进行（等它完成或看进度）")
    ids = body.get("ids") or None
    try:
        projects = REGISTRY.select(ids=ids)
    except KeyError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    if not projects:
        raise HTTPException(status_code=400, detail="没有匹配的评测项目")
    runs = max(1, min(int(body.get("runs") or 3), 5))
    root = getattr(request.app.state, "eval_root", None) or _out_root()
    # 测试注入 fake factory（app.state.eval_llm_factory）；正式不设 = openai_from_env。
    # 线程外先取好：request 的生命周期不保证覆盖后台线程。
    llm_factory = getattr(request.app.state, "eval_llm_factory", None) or _default_factory()

    job: dict = {"state": "running", "started_at": time.time(),
                 "label": "+".join(p.id for p in projects), "runs": runs,
                 "log": [], "error": None, "report": None}
    _JOB["cur"] = job

    def _worker() -> None:
        from eval.run import run_batch

        def _say(text: str) -> None:
            job["log"].append(text)
            job["log"] = job["log"][-50:]   # 尾部 50 行（前端进度）

        try:
            out = asyncio.run(run_batch(projects, runs, llm_factory, root,
                                        on_progress=_say))
            job["report"] = out["report"]
            job["state"] = "done"
        except BaseException as exc:   # LLM 未配/网络/内部错 —— 全部落 job.error 不 500
            job["state"] = "error"
            job["error"] = f"{type(exc).__name__}: {exc}"
        finally:
            job["finished_at"] = time.time()

    threading.Thread(target=_worker, daemon=True, name="eval-run").start()
    return {"ok": True, "job": job}


def _out_root():
    from eval.run import OUT_ROOT

    return OUT_ROOT


def _default_factory():
    """与 CLI 同源（agent.talk.openai_from_env，.env 自动加载）。"""
    from agent.talk import openai_from_env

    return openai_from_env
