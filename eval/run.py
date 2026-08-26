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
from eval.archive import append_index, load_index, save_run
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


async def _run_project(project: Project, runs: int, llm_factory, out_dir: Path,
                       on_progress=None) -> tuple[list[dict], list[dict]]:
    """返回 (报告行, 索引行)。索引行是归档的入口（append-only，前端读这个）。"""
    say = on_progress or (lambda text: None)
    rows: list[dict] = []
    index_rows: list[dict] = []
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
        # 基础数据全保留（用户拍板 2026-08-25）：每 run 归档完整 RunResult + 判定明细
        rel = f"{out_dir.name}/{project.id}/run{run_no}"
        irow = save_run(run_dir, result, grades)
        irow.update({"project": project.id, "run_dir": rel})
        index_rows.append(irow)
        ok = sum(1 for g in grades if g.ok)
        say(f"  [{project.id}] run {run_no}/{runs}：{ok}/{len(grades)} 轴过"
            f"（outcome={result.meta.get('outcome')}）")
    return rows, index_rows


async def run_batch(projects: list, runs: int, llm_factory, out_root: Path,
                    on_progress=None) -> dict:
    """一批场景跑完（CLI main 与 API 触发共用体）。返回 {report, label, out_dir, failed}。

    on_progress(text)（可选）：每完成一个 run/阶段的进度行 —— CLI 传 print，
    API 传 job 状态收集器（前端轮询看进度）。
    """
    say = on_progress or (lambda text: None)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    label = "+".join(p.id for p in projects)
    # 目录名与 label 分离：全量一批 13 个场景时 label 300+ 字符，直接当目录名
    # 爆 Windows MAX_PATH=260（WinError 3，2026-08-26 run_eval.bat 实测）。目录名
    # 截断 + 尾 hash 保唯一；完整场景清单在 report.md/index.jsonl 里，不丢信息。
    dir_label = (label if len(label) <= 80
                 else label[:68] + f"...x{len(projects)}-{hash(label) & 0xFFFF:04x}")
    out_dir = out_root / f"{stamp}-{dir_label}"

    all_rows: list[dict] = []
    all_index: list[dict] = []
    for project in projects:
        say(f"[{project.id}] × {runs} runs")
        rows, index_rows = await _run_project(project, runs, llm_factory, out_dir,
                                              on_progress=say)
        all_rows.extend(rows)
        all_index.extend(index_rows)

    path = write_report(out_dir, label, all_rows)
    append_index(out_root, all_index, label, path)
    failed = [r for r in all_rows
              if any(not g.ok and g.passed is not None for g in r["grades"])]
    say(f"完成：{len(all_index)} runs，失败 {len(failed)} —— 报告 {path}")
    return {"report": str(path), "label": label, "out_dir": str(out_dir),
            "failed": len(failed)}


async def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="eval.run")
    ap.add_argument("ids", nargs="*", help="项目 id（缺省 = 全部）")
    ap.add_argument("--tags", nargs="*", help="按标签过滤")
    ap.add_argument("--runs", type=int, default=None, help="覆盖默认 N")
    ap.add_argument("--deterministic", action="store_true",
                    help="temp=0 + 建议 N=1（定位回归用，D11）")
    ap.add_argument("--list", action="store_true",
                    help="列出历史评测（读 index.jsonl 索引，不跑任何东西）")
    args = ap.parse_args(argv)

    if args.list:
        rows = load_index(OUT_ROOT)
        if not rows:
            print(f"还没有评测记录（索引：{OUT_ROOT / 'index.jsonl'}）")
            return 0
        print(f"{'时间':<20}{'项目':<24}{'run':<4}{'轴':<6}{'失败':<20}报告")
        for r in rows:
            failed = ",".join(r.get("failed_graders") or []) or "-"
            print(f"{r.get('ts', ''):<20}{r.get('project', '?'):<24}"
                  f"{str(r.get('run_no', '?')):<4}{r.get('passed', 0)}/{r.get('axes', 0):<4}"
                  f"{failed:<20}{r.get('report', '')}")
        return 0

    projects = REGISTRY.select(ids=args.ids or None, tags=args.tags or None)
    if not projects:
        print("没有匹配的评测项目", file=sys.stderr)
        return 2
    runs = args.runs or (1 if args.deterministic else DEFAULT_RUNS)

    out = await run_batch(projects, runs, _llm_factory(args.deterministic), OUT_ROOT,
                          on_progress=print)
    print(f"\n报告：{out['report']}")
    print(f"归档：{out['out_dir']}（result.json/grades.json × 全部 run）"
          f" + 索引 {OUT_ROOT / 'index.jsonl'}")
    return 1 if out["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
