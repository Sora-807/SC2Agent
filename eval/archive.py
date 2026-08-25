"""eval.archive：评测轨迹归档（用户拍板 2026-08-25：每次评测的基础数据全保留）。

目录布局（未来前端读这里，路径稳定）：

    runtime/eval/
      index.jsonl                     # append-only 索引：一行一个 (报告, 项目, run)
      <ts>-<label>/
        report.md                     # 人读报告
        results.jsonl                 # 摘要行（meta 摘要 + grades，向后兼容旧格式）
        prompts/<hash>.md             # 本版提示词全文快照（同 hash 不重写）
        <project>/run<N>/
          result.json                 # **完整 RunResult**（全量归档：工具序列/思考/
          #                            #  提案/segments/changes/workspace/会话终态；
          #                            #  messages 全量；提示词全文按 hash 在 prompts/）
          grades.json                 # 本 run 的判定明细
          traces/…                    # Tracer 原始落盘（messages.jsonl/reasoning blob）
          history.json                # AgentTalk 对话历史
          world/…                     # 帧目录 + proposals.jsonl（游戏轨迹）

runtime/ 在 .gitignore（与 traces/recordings 同规矩）——评测轨迹本地保留，不进库。
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from eval.result import Grade, RunResult


def save_run(run_dir: Path, result: RunResult, grades: list[Grade]) -> dict:
    """归档一个 run 的完整数据。返回索引行（不含 eval_root 相对路径字段由 caller 补）。"""
    meta = dict(result.meta)
    prompt_text = meta.pop("prompt_full_text", "")   # 全文在 prompts/<hash>.md，不重复存
    (run_dir / "result.json").write_text(json.dumps(
        {**result.to_dict(full=True), "meta": meta},
        ensure_ascii=False, indent=1), encoding="utf-8")
    (run_dir / "grades.json").write_text(json.dumps(
        [g.to_dict() for g in grades], ensure_ascii=False, indent=1), encoding="utf-8")
    return {
        "ts": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "project": None,                      # caller 填
        "run_no": meta.get("run_no"),
        "outcome": meta.get("outcome"),
        "llm_model": meta.get("llm_model"),
        "prompt_hash": meta.get("prompt_hash"),
        "seed_hash": meta.get("seed_hash"),
        "passed": sum(1 for g in grades if g.ok),
        "axes": len(grades),
        "failed_graders": [g.grader for g in grades if not g.ok],
        "run_dir": str(run_dir),              # 相对 eval_root（caller 换算）
    }


def append_index(eval_root: Path, rows: list[dict], label: str, report_path: Path) -> None:
    eval_root.mkdir(parents=True, exist_ok=True)
    with (eval_root / "index.jsonl").open("a", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps({
                **row,
                "label": label,
                "report": _rel(report_path, eval_root),
            }, ensure_ascii=False) + "\n")


def load_index(eval_root: Path) -> list[dict]:
    path = eval_root / "index.jsonl"
    if not path.is_file():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line:
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
    return out


def _rel(path: Path, root: Path) -> str:
    try:
        return str(path.resolve().relative_to(root.resolve())).replace("\\", "/")
    except ValueError:
        return str(path)
