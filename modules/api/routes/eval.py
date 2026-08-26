"""评测管线的前端面：场景注册表 + 运行记录 + 触发式跑批（2026-08-26 起）。

跑批两条入口同一份归档：CLI（`uv run python -m eval.run`）与 POST /api/eval/run
（前端评测页按钮）。API 版在后台线程跑（分钟级：LLM 依赖 + 每场景 N 次），
job 状态挂在 app.state.eval_job，前端轮询 overview 里的 job 字段看进度。

import 全部延迟到函数体：eval 包顶层（fixture）import api.app，模块级 import
会成环（routes → eval.run → scenarios → fixture → api.app 半初始化）。
"""
from __future__ import annotations

import asyncio
import json
import threading
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, Response

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


def _resolve_under(root: Path, run_dir: str) -> Path:
    """run_dir（相对 eval_root）→ 受控绝对路径。越界=400（绝对路径/`..`），
    不存在=404——错误语义拆开（I39 口径：别把「路径坏了」说成「没有」）。"""
    try:
        resolved = (root / run_dir).resolve()
        resolved.relative_to(root.resolve())
    except (ValueError, OSError):
        raise HTTPException(status_code=400, detail=f"run_dir 越界：{run_dir!r}") from None
    if not resolved.is_dir():
        raise HTTPException(status_code=404,
                            detail=f"run 不存在：{run_dir}（可用记录看 /api/eval/overview 的 runs 表）") from None
    return resolved


def _index_row(root: Path, run_dir: str) -> dict | None:
    """index.jsonl 里匹配 run_dir 的那行；中断批次的孤儿 run（批末才 append）→ None。"""
    from eval.archive import load_index

    for row in load_index(root):
        if row.get("run_dir") == run_dir:
            return row
    return None


# 注意注册顺序：/prompt 必须在 {run_dir:path} 之前，否则 path 转换器会把它吞进 run_dir
@router.get("/api/eval/runs/{run_dir:path}/prompt")
def eval_run_prompt(run_dir: str, request: Request) -> Response:
    """本 run 的提示词全文快照（D16）——text/plain，点开才取（懒加载）。

    快照在批次目录 prompts/<hash>.md，**批末**才写：中断批次的 run 没有 → 404
    （真实情况，不是异常路径）。
    """
    from eval.run import OUT_ROOT

    root = getattr(request.app.state, "eval_root", None) or OUT_ROOT
    rd = _resolve_under(root, run_dir)
    result_file = rd / "result.json"
    if not result_file.is_file():
        raise HTTPException(status_code=404, detail=f"run 没有 result.json：{run_dir}") from None
    meta = (json.loads(result_file.read_text(encoding="utf-8")).get("meta") or {})
    h = meta.get("prompt_hash")
    prompt_file = rd.parent.parent / "prompts" / f"{h}.md" if h else None
    if not prompt_file or not prompt_file.is_file():
        raise HTTPException(status_code=404,
                            detail="该批次未写提示词快照（批末才落盘——被中断的批次没有）") from None
    return Response(prompt_file.read_text(encoding="utf-8"),
                    media_type="text/plain; charset=utf-8")


@router.get("/api/eval/runs/{run_dir:path}")
def eval_run_detail(run_dir: str, request: Request, messages: int = 0) -> dict:
    """单 run 全量指标（PLAN-EVAL-FRONTEND 批 B：直读归档，不重跑不重算）。

    run_dir 来自 index 行原样（相对 eval_root，批次目录含 `+`——path 形态是
    字面量，query 里会被解码成空格，这是走 path-param 的原因）。messages 默认
    剥掉只返条数（极端归档 8 万+字符）；?messages=1 附 400 字/条摘要（与
    result.py 报告瘦身同款裁剪，单源）。index_row 匹配不到 = 中断批次的孤儿 run。
    """
    from eval.result import _clip_messages  # 同款裁剪逻辑，单源
    from eval.run import OUT_ROOT

    root = getattr(request.app.state, "eval_root", None) or OUT_ROOT
    rd = _resolve_under(root, run_dir)
    result_file = rd / "result.json"
    if not result_file.is_file():
        raise HTTPException(status_code=404, detail=f"run 没有 result.json：{run_dir}") from None
    result = json.loads(result_file.read_text(encoding="utf-8"))
    grades_file = rd / "grades.json"
    grades = (json.loads(grades_file.read_text(encoding="utf-8"))
              if grades_file.is_file() else [])
    msgs = result.pop("messages", [])
    return {
        **result,
        "messages_count": len(msgs),
        "messages": _clip_messages(msgs) if messages else [],
        "grades": grades,
        "index_row": _index_row(root, run_dir),
    }


def _default_factory():
    """与 CLI 同源（agent.talk.openai_from_env，.env 自动加载）。"""
    from agent.talk import openai_from_env

    return openai_from_env
