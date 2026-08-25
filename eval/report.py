"""eval.report：markdown 报告 + 提示词快照（D6/D16）。

落 `runtime/eval/<ts>-<label>/report.md`；每版提示词全文落 `prompts/<hash>.md`
（同 hash 不重写）——报告可跨版 diff，hash 双列（prompt_hash + seed_hash）。
"""
from __future__ import annotations

from pathlib import Path

from eval.result import Grade, RunResult


def write_report(out_dir: Path, label: str, rows: list[dict]) -> Path:
    """rows = [{project, task, run_no, result: RunResult, grades: [Grade]}]。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "results.jsonl").write_text(
        "\n".join(_row_json(r) for r in rows), encoding="utf-8")
    _dump_prompts(out_dir, rows)

    lines = [f"# eval 报告：{label}", ""]
    meta = rows[0]["result"].meta if rows else {}
    lines += [
        f"- 模型：{meta.get('llm_model') or '?'}　项目数：{len({r['project'] for r in rows})}"
        f"　总 run：{len(rows)}",
        f"- prompt_hash：`{meta.get('prompt_hash')}`　seed_hash：`{meta.get('seed_hash')}`（D16 双列）",
        "",
        "## 汇总（按项目 × 轴）",
        "",
        "| 项目 | 轴 | pass/run | 说明 |",
        "|---|---|---|---|",
    ]
    for proj, axis, passed, total, note in _summary(rows):
        lines.append(f"| {proj} | {axis} | {passed}/{total} | {note} |")

    lines += ["", "## 明细", ""]
    for r in rows:
        res: RunResult = r["result"]
        m = res.meta
        lines.append(f"### {r['project']} · run {m.get('run_no')}"
                     f"（outcome={m.get('outcome')}，{m.get('duration_s')}s，"
                     f"in={m.get('input_tokens')} out={m.get('output_tokens')}）")
        lines.append("")
        lines.append(f"> 任务：{r['task']}")
        lines.append("")
        for g in r["grades"]:
            mark = _mark(g)
            lines.append(f"- {mark} **[{g.axis}] {g.grader}**：{g.reason_zh}")
        lines.append("")
        tools = [t.get("tool") for t in res.tool_calls]
        lines.append(f"- 工具序列：{' → '.join(str(t) for t in tools) or '（无）'}")
        lines.append(f"- 提案数：{len(res.proposals)}　工作区写入：{len(res.workspace)} 个文件"
                     + (f"　（reasoning 截断 {m.get('reasoning_clipped')} 段）"
                        if m.get("reasoning_clipped") else ""))
        reply = (res.final_text or "").strip().replace("\n", " ")
        lines.append(f"- 回复摘录：{reply[:200] or '（空）'}")
        lines.append("")
    path = out_dir / "report.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def _row_json(row: dict) -> str:
    import json
    return json.dumps({
        "project": row["project"], "run_no": row["result"].meta.get("run_no"),
        "meta": {k: v for k, v in row["result"].meta.items() if k != "prompt_full_text"},
        "grades": [g.to_dict() for g in row["grades"]],
    }, ensure_ascii=False)


def _dump_prompts(out_dir: Path, rows: list[dict]) -> None:
    pdir = out_dir / "prompts"
    pdir.mkdir(exist_ok=True)
    for r in rows:
        m = r["result"].meta
        h = m.get("prompt_hash")
        text = m.get("prompt_full_text")
        if h and text and not (pdir / f"{h}.md").exists():
            (pdir / f"{h}.md").write_text(text, encoding="utf-8")


def _summary(rows: list[dict]) -> list[tuple]:
    agg: dict[tuple[str, str], list] = {}
    for r in rows:
        for g in r["grades"]:
            agg.setdefault((r["project"], g.axis), []).append(g)
    out = []
    for (proj, axis), grades in sorted(agg.items()):
        passed = sum(1 for g in grades if g.ok)
        score_graders = [g for g in grades if g.score is not None]
        note = ""
        if score_graders:
            avg = sum(g.score for g in score_graders) / len(score_graders)
            note = f"LLM 轴均分 {avg:.1f}/5"
        out.append((proj, axis, passed, len(grades), note))
    return out


def _mark(g: Grade) -> str:
    if g.passed is not None:
        return "✅" if g.passed else "❌"
    if g.score is not None:
        stars = round(g.score)
        return f"⭐{'⭐' * (stars - 1) if stars > 1 else ''}" if g.score else "—"
    return "—"
