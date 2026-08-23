"""对局记录（二十六轮）：live 帧流的落盘清单 + 复盘数据源。"""
from __future__ import annotations

import json
import re
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import PlainTextResponse

router = APIRouter()


def _scan_recording(path: Path) -> dict:
    """流扫一份录制文件补 envelopes/from/to（录制中或异常终止时 meta 没有终态）。"""
    envelopes = 0
    to_time = 0.0
    if path.is_file():
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                if not line.strip():
                    continue
                envelopes += 1
                try:
                    gt = float(json.loads(line).get("game_time", 0) or 0)
                except ValueError:
                    continue
                to_time = max(to_time, gt)
    return {"envelopes": envelopes, "from": 0.0, "to": to_time}


@router.get("/api/recordings")
def recordings_list(request: Request) -> list[dict]:
    """已落盘的对局记录（新→旧）。录制中的也列出（state=recording）。

    复盘从此有真数据源：夹具是手搓场景，录像是真开过的一局。
    meta 由 LiveSession 在开录/收尾时写；收尾带 envelopes/to_time，
    录制中的（或进程被杀没写终态的）扫文件流补 —— 文件是唯一真相源。
    """
    dirp: Path | None = request.app.state.recordings_dir
    if dirp is None or not dirp.is_dir():
        return []
    out: list[dict] = []
    for meta_path in sorted(dirp.glob("rec-*.meta.json"), reverse=True):
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if meta.get("state") == "recording" or "envelopes" not in meta:
            # 命名是 <rid>.meta.json / <rid>.jsonl —— 不能用 with_suffix
            # （它只剥最后一个后缀，会得到 <rid>.meta.jsonl 这种不存在的文件）
            meta.update(_scan_recording(
                meta_path.with_name(meta_path.name.replace(".meta.json", ".jsonl"))))
            meta["to"] = meta.get("to", meta.get("to_time", 0.0))
        meta.setdefault("id", meta_path.stem)
        out.append(meta)
    return out


@router.get("/api/recordings/{rid}/jsonl", response_class=PlainTextResponse)
def recording_jsonl(rid: str, request: Request) -> str:
    """一份记录的帧流（与夹具同格式：前端 JsonlFrameSource 直接吃）。"""
    if not re.fullmatch(r"[\w.-]+", rid):
        raise HTTPException(status_code=400, detail="记录 id 不合法")
    dirp: Path | None = request.app.state.recordings_dir
    path = dirp / f"{rid}.jsonl" if dirp is not None else None
    if path is None or not path.is_file():
        raise HTTPException(status_code=404, detail=f"没有对局记录 {rid!r}")
    return path.read_text(encoding="utf-8")
