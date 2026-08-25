"""eval.run：CLI 入口（PLAN §3.7/§3.14）。

用法：
    uv run python -m eval.run                    # 全部轻管线场景
    uv run python -m eval.run L1-gas-block       # 指定项目
    uv run python -m eval.run --tags live        # 按标签
    uv run python -m eval.run --runs 3           # 覆盖默认 N（D11：N=3 取稳态）

轻管线唯一外部依赖 = LLM provider（OPENAI_API_KEY / LLM_MODEL，与 serve_api 同源）。
"""
from __future__ import annotations

import argparse
import asyncio
import inspect
import os
import sys
from datetime import datetime
from pathlib import Path

import eval.scenarios  # noqa: F401 —— import 即注册
from eval.contracts import Project
from eval.registry import REGISTRY
from eval.report import write_report
from eval.runner import SingleRoundRunner

DEFAULT_RUNS = 3
OUT_ROOT = Path(__file__).resolve().parent.parent / "runtime" / "eval"


def _llm_factory(deterministic: bool):
    from agent.talk import openai_from_env

    def factory():
        if deterministic:
            # D11：回归二分档（best-effort —— provider 支持 temperature 覆盖时生效）
            os.environ.setdefault("LLM_TEMPERATURE", "0")
        return openai_from_env()
    return factory


async def _run_project(project: Project, runs: int, llm_factory, out_dir: Path) -> list[dict]:
    rows: list[dict] = []
    for run_no in range(1, runs + 1):
        run_dir = out_dir / project.id / f"run{run_no}"
        run_dir.mkdir(parents=True, exist_ok=True)
        tmp = run_dir / "world"
        tmp.mkdir(exist_ok=True)
        world = project.fixture.setup(tmp)
        try:
            result = await project.runner.run(world, project.task, llm_factory, run_dir)
            result.meta["run_no"] = run_no
            # judge 类 grader 是 async（LLM client.complete 协程）——awaitable 就 await
            grades = []
            for g in project.graders:
                out = g.grade(result, world=world)
                if inspect.isawaitable(out):
                    out = await out
                grades.append(out)
        finally:
            project.fixture.teardown(world)
        rows.append({"project": project.id, "task": project.task.note or project.task.text,
                     "result": result, "grades": grades})
        ok = sum(1 for g in grades if g.ok)
        print(f"  [{project.id}] run {run_no}/{runs}：{ok}/{len(grades)} 轴过"
              f"（outcome={result.meta.get('outcome')}）")
    return rows


async def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="eval.run")
    ap.add_argument("ids", nargs="*", help="项目 id（缺省 = 全部）")
    ap.add_argument("--tags", nargs="*", help="按标签过滤")
    ap.add_argument("--runs", type=int, default=None, help="覆盖默认 N")
    ap.add_argument("--deterministic", action="store_true",
                    help="temp=0 + 建议 N=1（定位回归用，D11）")
    args = ap.parse_args(argv)

    projects = REGISTRY.select(ids=args.ids or None, tags=args.tags or None)
    if not projects:
        print("没有匹配的评测项目", file=sys.stderr)
        return 2
    runs = args.runs or (1 if args.deterministic else DEFAULT_RUNS)

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    label = "+".join(p.id for p in projects)
    out_dir = OUT_ROOT / f"{stamp}-{label}"
    llm_factory = _llm_factory(args.deterministic)

    all_rows: list[dict] = []
    for project in projects:
        print(f"[{project.id}] × {runs} runs")
        all_rows.extend(await _run_project(project, runs, llm_factory, out_dir))

    path = write_report(out_dir, label, all_rows)
    print(f"\n报告：{path}")
    failed = [r for r in all_rows
              if any(not g.ok and g.passed is not None for g in r["grades"])]
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
