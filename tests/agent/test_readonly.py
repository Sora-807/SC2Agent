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
    """顶层小文件挂、子目录（快照/检查点）与大文件绝不挂 —— 2026-08-23 指数增长事故的锁。"""
    root = tmp_path / "traces" / "2026-01-01_run1"
    root.mkdir(parents=True)
    (root / "trace.md").write_text("# 轨迹", encoding="utf-8")
    (root / "trace.html").write_text("<html>可视化</html>", encoding="utf-8")
    snap = root / "workspace_snapshots"
    snap.mkdir()
    (snap / "initial.json").write_text('{"嵌套快照": true}', encoding="utf-8")
    (root / "huge.json").write_text("x" * (2 * 1024 * 1024 + 1), encoding="utf-8")
    area = TraceArea(tmp_path / "traces")
    # 顶层小文件可挂；子目录快照与超限大文件不进清单
    assert area.list_paths() == ["traces/2026-01-01_run1/trace.md"]
    assert area.read("traces/2026-01-01_run1/trace.md") == "# 轨迹"
    with pytest.raises(WorkspaceError, match="白名单"):
        area.read("traces/2026-01-01_run1/trace.html")
    with pytest.raises(WorkspaceError, match="白名单|上限"):
        area.read("traces/2026-01-01_run1/workspace_snapshots/initial.json")
    with pytest.raises(WorkspaceError, match="上限"):
        area.read("traces/2026-01-01_run1/huge.json")
    with pytest.raises(WorkspaceError):
        area.read("traces/../../etc/passwd")


def _workspace(tmp_path: Path, client: ApiClient) -> ApiWorkspace:
    return ApiWorkspace(
        client, tmp_path / "scratch", ChangeLog(),
        readonly=default_areas(client=client, trace_root=tmp_path / "traces",
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


def _map_plans_dir(tmp_path: Path) -> Path:
    d = tmp_path / "map-plans"
    d.mkdir()
    # 批 2 双分支形态：一份 = bl+tr（夹具 bl 侧有 D1，tr 侧空）
    (d / "layout.yaml").write_text(
        "id: layout\ntitle_zh: 出厂布局\nmap_name: LadderMap\n"
        "spawns:\n"
        "  bl:\n"
        "    origin: [30, 30]\n"
        "    anchor: [30, 30]\n"
        "    build_slots:\n"
        "      D1: {pos: [40.5, 32.5], size: 2, kind: supply, alias_zh: 补给站1}\n"
        "    pos_marks: {}\n"
        "  tr:\n"
        "    origin: [131, 127]\n"
        "    anchor: [131, 127]\n"
        "    build_slots: {}\n"
        "    pos_marks: {}\n", encoding="utf-8")
    return d


def test_maps_area_index_and_region(tmp_path: Path):
    from agent.readonly import MapsArea

    area = MapsArea(None, _map_plans_dir(tmp_path))
    assert area.list_paths() == ["maps/index.md"]
    idx = area.read("maps/index.md")
    assert "`live`" in idx and "layout" in idx and "示例" in idx
    grid = area.read("maps/layout/39_31_41_34.md")
    assert "D1" in grid and "词表" in grid
    # live 无会话（client=None → session() 失败被兜住）→ 退出厂布局并注明
    live = area.read("maps/live/39_31_41_34.md")
    assert "无会话" in live and "D1" in live


def test_maps_area_bad_paths_and_unknown_source(tmp_path: Path):
    from agent.readonly import MapsArea

    area = MapsArea(None, _map_plans_dir(tmp_path))
    with pytest.raises(WorkspaceError, match="约定 maps/"):
        area.read("maps/layout/not-a-bbox.md")
    with pytest.raises(WorkspaceError, match="没有地图源"):
        area.read("maps/nope/1_2_3_4.md")
    with pytest.raises(WorkspaceError, match="step"):
        area.read("maps/layout/0_0_40_40.md")          # 超网格上限：建议加步长


def test_maps_area_mounted_in_workspace(tmp_path: Path):
    client = ApiClient(base="http://127.0.0.1:1")
    from agent.readonly import default_areas

    ws = ApiWorkspace(
        client, tmp_path / "scratch", ChangeLog(),
        readonly=default_areas(client=client, trace_root=tmp_path / "traces",
                               recordings_dir=_recordings_dir(tmp_path),
                               proposals_log=tmp_path / "proposals.jsonl",
                               map_plans_dir=_map_plans_dir(tmp_path)))
    assert "maps/index.md" in ws._list_file_paths()
    assert "D1" in ws.read_text("maps/layout/39_31_41_34.md")
    with pytest.raises(WorkspaceError, match="只读区"):
        ws.write_text("maps/layout/1_1_2_2.md", "篡改")


def test_workspace_snapshot_excludes_readonly_and_rest(tmp_path: Path):
    """引擎每轮起止都会 snapshot()：只搬 scratch，历史只读区与 REST 区绝不进快照
    （2026-08-23 事故：traces 快照嵌快照指数到 4.2GB → MemoryError/假性停滞）。"""
    client = ApiClient(base="http://127.0.0.1:1")
    from agent.readonly import default_areas

    scratch = tmp_path / "scratch"
    scratch.mkdir()
    (scratch / "memory" ).mkdir()
    (scratch / "memory" / "user-preferences.md").write_text("偏好", encoding="utf-8")
    ws = ApiWorkspace(
        client, scratch, ChangeLog(),
        readonly=default_areas(client=client, trace_root=tmp_path / "traces",
                               recordings_dir=_recordings_dir(tmp_path),
                               proposals_log=tmp_path / "proposals.jsonl",
                               map_plans_dir=_map_plans_dir(tmp_path)))
    snap = ws.snapshot()
    assert snap == {"memory/user-preferences.md": "偏好"}


def _dual_dir(tmp_path: Path) -> Path:
    d = tmp_path / "map-plans"
    d.mkdir(exist_ok=True)
    (d / "dual.yaml").write_text(
        "id: dual\ntitle_zh: 双分支\nmap_name: LadderMap\n"
        "spawns:\n"
        "  bl:\n    origin: [48.5, 28.5]\n    anchor: [48.5, 28.5]\n"
        "    build_slots:\n"
        "      D1: {pos: [40.5, 32.5], size: 2, kind: supply}\n    pos_marks: {}\n"
        "  tr:\n    origin: [131.5, 127.5]\n    anchor: [131.5, 127.5]\n"
        "    build_slots:\n"
        "      D1: {pos: [130.5, 106.5], size: 2, kind: supply}\n    pos_marks: {}\n",
        encoding="utf-8")
    return d


def test_maps_area_picks_side_by_bbox_and_suffix(tmp_path: Path):
    """2026-08-24 修：maps/<源>/ 以前永远 bl 侧（提示词说『默认』实为只能）——
    现按 bbox 自动选侧（中心 x ≥ 半场 → tr），显式 maps/<源>@bl|tr/ 覆盖。"""
    from agent.readonly import MapsArea

    area = MapsArea(None, _dual_dir(tmp_path))
    tr = area.read("maps/dual/128_104_134_110.md")          # tr 坐标 → 自动 tr
    assert "tr 侧（bbox 自动" in tr and "130" in tr
    bl = area.read("maps/dual/38_30_44_36.md")              # bl 坐标 → 自动 bl
    assert "bl 侧（bbox 自动" in bl
    assert "D1" in bl                                       # bl 的 D1 在框内
    explicit = area.read("maps/dual@tr/38_30_44_36.md")     # 显式 tr + bl 框
    assert "tr 侧（指定" in explicit
    assert "D1" not in explicit.split("# 区域", 1)[1]        # tr 的 D1 不在 bl 框里（如实空）


def test_maps_area_legacy_single_branch_still_works(tmp_path: Path):
    from agent.readonly import MapsArea

    area = MapsArea(None, _map_plans_dir(tmp_path))          # 既有夹具 = 双分支 layout
    t = area.read("maps/layout/39_31_41_34.md")
    assert "D1" in t
