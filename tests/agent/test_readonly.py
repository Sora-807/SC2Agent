"""agent.readonly + ApiWorkspace 只读区（I20 文件契约闭环的回归锁）。

锁四件事：① 只读区出现在 ls 里、能 read；② write 一律拒绝（历史不可变）；
③ 录像原始 jsonl 刻意不挂（大文件保护）且摘要可懒生成；④ traces 白名单外
（html）与越区路径都被拒。
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "modules"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "vendor"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest
from agentic.workspace.workspace import WorkspaceError

from agent.client import ApiClient
from agent.readonly import RecordingsArea, SingleFileArea, TraceArea, default_areas
from agent.workspace import ApiWorkspace, ChangeLog


def _recordings_dir(tmp_path: Path) -> Path:
    d = tmp_path / "recordings"
    d.mkdir()
    (d / "rec-x.jsonl").write_text("\n".join(json.dumps(f) for f in [
        {"topic": "frame/world", "game_time": 0.0, "payload": {
            "economy": {"minerals": 50, "vespene": 0, "supply_used": 8, "supply_cap": 13},
            "units": [{"tag": 1, "stable_id": "terran/scv", "owner": "self",
                       "build_progress": 1.0, "footprint": None}] * 8}},
        {"topic": "frame/world", "game_time": 30.0, "payload": {
            "economy": {"minerals": 90, "vespene": 0, "supply_used": 10, "supply_cap": 13},
            "units": []}},
    ]), encoding="utf-8")
    (d / "rec-x.meta.json").write_text(json.dumps({
        "id": "rec-x", "state": "已结束", "to": 30.0, "envelopes": 2,
        "my_race_zh": "人族", "enemy_race_zh": "神族", "map": "LadderMap"}), encoding="utf-8")
    return d


def test_recordings_area_index_and_lazy_summary(tmp_path: Path):
    area = RecordingsArea(_recordings_dir(tmp_path))
    assert area.list_paths() == ["recordings/index.md", "recordings/rec-x.md"]
    idx = area.read("recordings/index.md")
    assert "rec-x" in idx and "人族 vs 神族" in idx
    # .md 不在磁盘上 → 从 jsonl 懒生成并落盘
    summary = area.read("recordings/rec-x.md")
    assert "时长 30 秒" in summary
    assert (tmp_path / "recordings" / "rec-x.md").is_file()


def test_recordings_area_rejects_raw_jsonl(tmp_path: Path):
    area = RecordingsArea(_recordings_dir(tmp_path))
    with pytest.raises(WorkspaceError, match="刻意不挂"):
        area.read("recordings/rec-x.jsonl")
    assert "recordings/rec-x.jsonl" not in area.list_paths()


def test_trace_area_whitelist_and_escape(tmp_path: Path):
    root = tmp_path / "traces" / "2026-01-01_run1"
    root.mkdir(parents=True)
    (root / "trace.md").write_text("# 轨迹", encoding="utf-8")
    (root / "trace.html").write_text("<html>大文件</html>", encoding="utf-8")
    area = TraceArea(tmp_path / "traces")
    assert area.list_paths() == ["traces/2026-01-01_run1/trace.md"]
    assert area.read("traces/2026-01-01_run1/trace.md") == "# 轨迹"
    with pytest.raises(WorkspaceError, match="白名单"):
        area.read("traces/2026-01-01_run1/trace.html")
    with pytest.raises(WorkspaceError):
        area.read("traces/../../etc/passwd")


def _workspace(tmp_path: Path, client: ApiClient) -> ApiWorkspace:
    return ApiWorkspace(
        client, tmp_path / "scratch", ChangeLog(),
        readonly=default_areas(trace_root=tmp_path / "traces",
                               recordings_dir=_recordings_dir(tmp_path),
                               proposals_log=tmp_path / "proposals.jsonl"))


def test_workspace_mounts_and_guards_readonly(tmp_path: Path, monkeypatch):
    (tmp_path / "proposals.jsonl").write_text(
        '{"id": "p1", "status": "已接受", "title_zh": "补精炼厂"}\n', encoding="utf-8")
    client = ApiClient(base="http://127.0.0.1:1")   # 不发请求：只测只读区与 write 拒绝
    ws = _workspace(tmp_path, client)

    listing = ws._list_file_paths()
    assert "recordings/index.md" in listing
    assert "recordings/rec-x.md" in listing
    assert "proposals/log.jsonl" in listing
    assert not any(p.endswith(".jsonl") and p.startswith("recordings/") for p in listing)

    assert "已接受" in ws.read_text("proposals/log.jsonl")
    assert "时长 30 秒" in ws.read_text("recordings/rec-x.md")

    for victim in ("recordings/rec-x.md", "proposals/log.jsonl", "traces/x/trace.md"):
        with pytest.raises(WorkspaceError, match="只读区"):
            ws.write_text(victim, "篡改历史")


def test_scratch_cannot_shadow_readonly_prefix(tmp_path: Path):
    client = ApiClient(base="http://127.0.0.1:1")
    ws = _workspace(tmp_path, client)
    # scratch 里手工放一个 recordings/ 假文件 —— 不该出现在 ls（只读区是唯一语义）
    (tmp_path / "scratch" / "recordings").mkdir(parents=True, exist_ok=True)
    (tmp_path / "scratch" / "recordings" / "fake.md").write_text("假", encoding="utf-8")
    assert "recordings/fake.md" not in ws._list_file_paths()
    with pytest.raises(WorkspaceError, match="只读区"):
        ws.write_text("recordings/fake.md", "写不进去")


def test_single_file_area_missing_is_error_not_empty(tmp_path: Path):
    area = SingleFileArea("proposals/log.jsonl", tmp_path / "none.jsonl")
    assert area.list_paths() == []
    with pytest.raises(WorkspaceError):
        area.read("proposals/log.jsonl")
