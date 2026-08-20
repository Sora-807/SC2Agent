"""view.jsonl：帧序列读写。

**live 推送、复盘录制、离线夹具用同一格式**（决策 U1/U2 的落点）：
一行一条信封 JSON。因此"复盘"不是另做一套，而是换一个帧源。
"""
from __future__ import annotations

import json
from collections.abc import Iterable, Iterator
from pathlib import Path


def write_frames(path, frames: Iterable[dict]) -> int:
    """写 JSONL，返回条数。父目录自动建。"""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with p.open("w", encoding="utf-8", newline="\n") as f:
        for frame in frames:
            f.write(json.dumps(frame, ensure_ascii=False, separators=(",", ":")))
            f.write("\n")
            n += 1
    return n


def read_frames(path) -> Iterator[dict]:
    """逐行读 JSONL。非法行**直接抛**并带行号（不静默跳过：坏帧要能被发现）。"""
    p = Path(path)
    with p.open("r", encoding="utf-8") as f:
        for lineno, line in enumerate(f, start=1):
            s = line.strip()
            if not s:
                continue
            try:
                yield json.loads(s)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{p}:{lineno} 不是合法 JSON：{exc}") from exc
