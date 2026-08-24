"""PLAN-V2 批 5 行为锁：catalog 只读区 / production-plans 改名（别名）/ scouting 派生 / bootstrap。"""
import asyncio
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agent.bootstrap import ensure_defaults
from agent.readonly import CatalogArea
from api.app import create_app
from game.catalog import load_all

CAT = load_all()


def test_catalog_area_lists_and_renders_three_races():
    area = CatalogArea()
    paths = area.list_paths()
    assert "catalog/index.md" in paths
    assert "catalog/terran/units.md" in paths and "catalog/zerg/units.md" in paths
    assert "catalog/protoss/addons.md" not in paths, "虫神无挂件"
    units = area.read("catalog/terran/units.md")
    assert "terran/siegetank" in units and "攻城坦克" in units
    assert units.count("|") > 20, "是 markdown 表格"
    # 参考级 9 字段：虫族没有挂件列（表头少一列）
    zerg = area.read("catalog/zerg/units.md")
    assert "挂件要求" not in zerg and "挂件要求" in units
    with pytest.raises(Exception):
        area.read("catalog/terran/nope.md")


def test_plans_rename_with_legacy_alias(tmp_path):
    from agent.client import ApiClient
    from agent.tools import ChangeLog
    from agent.workspace import ApiWorkspace

    c = TestClient(create_app(tmp_path / "f", tmp_path / "p.jsonl",
                              plans_dir=tmp_path / "plans",
                              map_plans_dir=tmp_path / "mp"))
    r = c.post("/api/plans", json={"id": "r1", "title_zh": "t", "queue": [
        {"op": "train", "type": "terran/scv", "count": 1}]})
    assert r.status_code == 200, r.text
    from tests.agent.test_workspace import _client_for

    ws = ApiWorkspace(_client_for(c), tmp_path / "s", ChangeLog())
    ws.read_text("production-plans/r1.yaml")   # 先读后写（文件契约守卫）
    ws.write_text("production-plans/r1.yaml",
                  "id: r1\ntitle_zh: t\nmap: LadderMap\nspawn: bl\nqueue:\n"
                  "- {op: train, type: terran/scv, count: 1}\n")
    assert "production-plans/r1.yaml" in ws._list_file_paths()
    # 旧名可读（agent 历史笔记兼容），但清单只列新名
    assert "id: r1" in ws.read_text("plans/r1.yaml")
    # 旧名 read/write 别名可用；清单（vendor visible_paths 面）只列新名
    assert "plans/r1.yaml" not in ws.visible_paths()


def test_scouting_counts_grouped_scvs():
    """D3：侦查 = 编入 flow 组的 SCV（group_id 非空）—— 工人五分不再恒 0。"""
    from view.observe import _worker_split

    world = {"units": [
        {"owner": "self", "stable_id": "terran/scv", "group_id": "G_SCOUT"},
        {"owner": "self", "stable_id": "terran/scv", "group_id": "G_SCOUT"},
        {"owner": "self", "stable_id": "terran/scv", "group_id": None},
        {"owner": "self", "stable_id": "terran/marine", "group_id": "G_INF"},
    ]}
    econ = {"tasks": [{"task": "mineral", "actual": 1}]}
    w = _worker_split(world, econ, {"in_flight": []})
    assert w["scouting"] == 2 and w["total"] == 3
    assert w["mineral"] == 1 and w["other"] == 0


def test_bootstrap_creates_missing_only_and_idempotent(tmp_path):
    created = ensure_defaults(tmp_path)
    assert set(created) == {"workspace.md", "system/prompt.md"}
    assert (tmp_path / "system" / "prompt.md").read_text(encoding="utf-8").lstrip().startswith(("#", "你", "你", "["))
    again = ensure_defaults(tmp_path)
    assert again == [], "只补缺失：第二次运行为空"
    # 用户改过的不覆盖
    (tmp_path / "workspace.md").write_text("用户自己写的", encoding="utf-8")
    assert ensure_defaults(tmp_path) == []
    assert "用户自己写的" in (tmp_path / "workspace.md").read_text(encoding="utf-8")
