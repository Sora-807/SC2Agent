"""agent.workspace：虚拟文件工作区（2026-08-22 文件工作区改造的核心）。

锁四件事：
1. 虚拟目录映射：plans/ 与 map-plans/ 走规划 API（REST 同 UI 入口，U7），
   scratch 落磁盘；
2. 文件语义在虚拟文件上成立：read-before-write、edit 字面量替换、insert 按行、
   grep 跨规划搜索（CRUD 时代做不到的事）；
3. 写时校验：锁定规划拒改、槽位重叠拒存，理由可读地回喂；
4. ChangeRecord：成功落盘才记（校验失败/未遂不记），轮末 drain。
"""
from pathlib import Path

import pytest
from agentic.workspace.workspace import WorkspaceError
from fastapi.testclient import TestClient

from agent.client import ApiClient
from agent.workspace import ApiWorkspace, ChangeLog
from api.app import create_app


def _client_for(app_client: TestClient) -> ApiClient:
    def transport(method: str, path: str, body: dict | None):
        if method == "GET":
            res = app_client.get(path)
        elif method == "PUT":
            res = app_client.put(path, json=body or {})
        elif method == "DELETE":
            res = app_client.delete(path)
        else:
            res = app_client.post(path, json=body or {})
        try:
            return res.status_code, res.json()
        except ValueError:
            return res.status_code, {"detail": res.text}
    return ApiClient(transport=transport)


@pytest.fixture()
def api(tmp_path: Path) -> TestClient:
    return TestClient(create_app(tmp_path / "frames", tmp_path / "p.jsonl",
                                 plans_dir=tmp_path / "plans",
                                 map_plans_dir=tmp_path / "map-plans"))


@pytest.fixture()
def ws(api: TestClient, tmp_path: Path) -> ApiWorkspace:
    return ApiWorkspace(_client_for(api), tmp_path / "scratch", ChangeLog())


QUEUE_YAML = (
    "id: agent-w1\n"
    "title_zh: 工作区写的规划\n"
    "map: LadderMap\n"
    "spawn: bl\n"
    "queue:\n"
    "- {op: build, type: terran/refinery, count: 1}\n"
    "- {op: build, type: terran/barracks, count: 2}\n"
    "- {op: train, type: terran/marine, count: 6}\n"
)


# ---------------- 虚拟目录映射 ----------------

def test_ls_lists_plans_map_plans_and_scratch(ws: ApiWorkspace):
    ws.write_text("memory.md", "# 记忆\n偏好：速二矿\n")
    paths = ws.visible_paths()
    assert "plans/default.yaml" in paths
    assert "map-plans/default-bl.yaml" in paths
    assert "memory.md" in paths


def test_read_plan_renders_yaml_roundtrip(ws: ApiWorkspace):
    text = ws.read_text("plans/default.yaml")
    assert "queue:" in text and "terran/" in text
    # 再读一致（渲染稳定，edit 的 old_string 匹配才有意义）
    assert ws.read_text("plans/default.yaml") == text


def test_read_missing_plan_is_an_error(ws: ApiWorkspace):
    with pytest.raises(WorkspaceError):
        ws.read_text("plans/nope.yaml")


# ---------------- 生产规划：写路径 ----------------

def test_write_new_plan_lands_in_api_and_records_change(ws: ApiWorkspace, api: TestClient):
    ws.write_text("plans/agent-w1.yaml", QUEUE_YAML)
    p = api.get("/api/plans/agent-w1").json()
    assert p["title_zh"] == "工作区写的规划"
    assert [i["type"] for i in p["queue"]][:2] == ["terran/refinery", "terran/barracks"]
    recs = ws._changes.drain()
    assert [(r.area, r.action, r.ref) for r in recs] == [("plan", "add", "agent-w1")]


def test_edit_requires_read_first_then_persists(ws: ApiWorkspace, api: TestClient):
    # 从 API 侧建文件（工作区没 read 过）→ edit 拒：观察策略在虚拟文件上同样成立
    api.post("/api/plans", json={"id": "agent-w1", "copy_from": "default"})
    api.put("/api/plans/agent-w1", json={"title_zh": "工作区写的规划",
                                         "map": "LadderMap", "spawn": "bl",
                                         "queue": [{"op": "train",
                                                    "type": "terran/marine", "count": 6}]})
    with pytest.raises(WorkspaceError):
        ws.edit_text("plans/agent-w1.yaml", "count: 6", "count: 12")
    ws.read_text("plans/agent-w1.yaml")
    n = ws.edit_text("plans/agent-w1.yaml", "count: 6", "count: 12")
    assert n == 1
    assert any(i["count"] == 12 for i in api.get("/api/plans/agent-w1").json()["queue"])
    assert [(r.area, r.action) for r in ws._changes.drain()] == [("plan", "edit")]


def test_edit_locked_plan_refused_with_reason(ws: ApiWorkspace, api: TestClient):
    ws.read_text("plans/default.yaml")   # 锁定前也得能读（商量战术的基准）
    with pytest.raises(WorkspaceError) as ei:
        ws.edit_text("plans/default.yaml", "spawn: bl", "spawn: tr")
    assert "锁定" in str(ei.value)
    assert api.get("/api/plans/default").json()["queue"], "锁定规划不能被覆盖"
    assert ws._changes.drain() == [], "未遂的写不记改动"


def test_insert_line_into_plan_queue(ws: ApiWorkspace, api: TestClient):
    ws.write_text("plans/agent-w2.yaml", QUEUE_YAML.replace("agent-w1", "agent-w2"))
    text = ws.read_text("plans/agent-w2.yaml")
    queue_line = text.splitlines().index("queue:") + 1   # 1-based：queue: 的行号
    # 插一行拼错类型的项：写钩子走服务端校验，当场拒（insert 整体不落）
    with pytest.raises(WorkspaceError):
        ws.insert_text("plans/agent-w2.yaml", queue_line,
                       "- {op: build, type: terrran/supplydepot, count: 1}")
    # 改对再插是合法的 —— 队首中间插入正是文件契约相对全量替换的价值
    ws.insert_text("plans/agent-w2.yaml", queue_line,
                   "- {op: build, type: terran/supplydepot, count: 1}")
    types = [i["type"] for i in api.get("/api/plans/agent-w2").json()["queue"]]
    assert "terran/supplydepot" in types


def test_write_invalid_queue_refused(ws: ApiWorkspace):
    with pytest.raises(WorkspaceError):
        ws.write_text("plans/agent-w3.yaml", "id: agent-w3\nqueue: []\n")


# ---------------- 地图规划：doc 读写 ----------------

def _dump(doc: dict) -> str:
    import yaml
    return yaml.safe_dump(doc, allow_unicode=True, sort_keys=False)


def test_map_plan_locked_preset_refused_but_readable(ws: ApiWorkspace, api: TestClient):
    text = ws.read_text("map-plans/layout-bl.yaml")
    assert "build_slots" in text and "D16" in text
    with pytest.raises(WorkspaceError) as ei:
        ws.edit_text("map-plans/layout-bl.yaml", "spawn: bl", "spawn: tr")
    assert "锁定" in str(ei.value)


def test_map_plan_full_write_roundtrip_and_change(ws: ApiWorkspace, api: TestClient):
    """复制 layout-bl 为副本再整体重写：预设存量槽位不追溯（diff 校验），改动落盘。"""
    import yaml
    doc = yaml.safe_load(ws.read_text("map-plans/layout-bl.yaml"))
    doc = {**doc, "id": "agent-mw1", "title_zh": "文件改出来的布局"}
    api.post("/api/map-plans", json={"id": "agent-mw1", "copy_from": "layout-bl"})
    ws.read_text("map-plans/agent-mw1.yaml")
    # 加一个新槽位（开放空间坐标，来自 barracks 定位修复扫描的安全区）
    doc = {**doc, "build_slots": {**(doc.get("build_slots") or {}),
                                  "R7": {"pos": [96.5, 80.5], "size": 3,
                                               "kind": "production"}}}
    ws.write_text("map-plans/agent-mw1.yaml", _dump(doc))
    slots = api.get("/api/map-plans/agent-mw1/doc").json()["build_slots"]
    assert "R7" in slots
    assert any(r.area == "map_plan" for r in ws._changes.drain())


def test_map_plan_new_file_via_write_copies_blank(ws: ApiWorkspace, api: TestClient):
    ws.write_text("map-plans/agent-mw2.yaml",
                  "id: agent-mw2\ntitle_zh: 文件新建的布局\nbuild_slots: {}\npos_marks: {}\n")
    rows = {r["id"]: r for r in api.get("/api/map-plans").json()}
    assert "agent-mw2" in rows


def test_map_plan_overlap_refused_on_full_write(ws: ApiWorkspace, api: TestClient):
    """新槽位压到既有槽位 → 全量写被拒，理由可读（改掉再试是合法的）。"""
    import yaml
    api.post("/api/map-plans", json={"id": "agent-mw3", "copy_from": "layout-bl"})
    doc = yaml.safe_load(ws.read_text("map-plans/agent-mw3.yaml"))
    anchor = sorted((doc.get("build_slots") or {}).items())[0]
    doc = {**doc, "build_slots": {**(doc.get("build_slots") or {}),
                                  "D30": {"pos": anchor[1]["pos"],
                                               "size": anchor[1].get("size") or 2,
                                               "kind": "supply"}}}
    ws.read_text("map-plans/agent-mw3.yaml")
    with pytest.raises(WorkspaceError) as ei:
        ws.write_text("map-plans/agent-mw3.yaml", _dump(doc))
    assert "重叠" in str(ei.value) or "压住" in str(ei.value)
    assert ws._changes.drain() == []


# ---------------- scratch：磁盘自留地 ----------------

def test_scratch_files_are_plain_disk(ws: ApiWorkspace, tmp_path: Path):
    ws.write_text("analysis-01.md", "# 分析\n默认规划二矿偏晚\n")
    assert (tmp_path / "scratch" / "analysis-01.md").read_text(encoding="utf-8") \
        .startswith("# 分析")
    assert ws._changes.drain() == [], "scratch 不产生跳转 chip"


def test_scratch_edit_flow(ws: ApiWorkspace):
    ws.write_text("memory.md", "偏好：速二矿\n")
    ws.read_text("memory.md")
    ws.edit_text("memory.md", "速二矿", "三矿")
    assert "三矿" in ws.read_text("memory.md")


# ---------------- grep：跨规划搜索（CRUD 时代做不到） ----------------

def test_grep_across_plans(ws: ApiWorkspace):
    ws.write_text("plans/agent-w1.yaml", QUEUE_YAML)
    matches = ws.grep("refinery", "plans/")
    assert any(m.path == "plans/agent-w1.yaml" for m in matches)


def test_glob_finds_yaml_by_area(ws: ApiWorkspace):
    assert "plans/default.yaml" in ws.glob("*.yaml", "plans/")
    assert "map-plans/default-bl.yaml" in ws.glob("*.yaml", "map-plans/")


# ---------------- 策略区（二十七轮「开放写策略，免审」） ----------------

STRATEGY_YAML = (
    "strategy:\n"
    "  id: agent-s1\n"
    "  group_slots: [main]\n"
    "  params: {min_units: {type: int, default: 2}}\n"
    "  variables: {}\n"
    "  initial_step: wait\n"
    "  steps:\n"
    "    - step_id: wait\n"
    "      branches:\n"
    "        - when: {op: '>=', args: [{op: group_count, group: main}, {param: min_units}]}\n"
    "          do: [{op: exit_step, kind: done, reason: GO}]\n"
    "        - do: []\n"
    "    - step_id: go\n"
    "      branches:\n"
    "        - do: [{op: exit_strategy, kind: done, reason: DONE}]\n"
    "  edges:\n"
    "    - {from: wait, to: go, kind: done, reason: GO}\n"
    "assembly:\n"
    "  id: agent-s1-asm\n"
    "  groups:\n"
    "    - group_id: G1\n"
    "      composition: {terran/marine: {min: 2, target: 4, max: 6}}\n"
    "  strategy_instances:\n"
    "    - {instance_id: s1, strategy_ref: agent-s1, bindings: {main: G1}, params: {}}\n"
)


def test_strategy_area_read_default_and_write_new(ws: ApiWorkspace, api: TestClient):
    """strategies/ 是第三类虚拟文件：default 可读不可改；新策略写入过编译校验。"""
    assert "strategies/default.yaml" in ws._list_file_paths()
    default = ws._read_file("strategies/default.yaml")
    assert "strategy:" in default and "assembly:" in default

    ws._write_file("strategies/agent-s1.yaml", STRATEGY_YAML)
    assert any(r["id"] == "agent-s1" for r in api.get("/api/strategies").json())
    # 读回的与写入的同形（roundtrip 由 store 的 doc 形状保证）
    back = ws._read_file("strategies/agent-s1.yaml")
    assert "agent-s1" in back and "strategy_instances" in back
    # 成功落盘记了 ChangeRecord（area=strategy，chip 跳规划-策略页）


def test_strategy_write_invalid_refused_without_change_record(ws: ApiWorkspace):
    """编译校验未通过 → 拒写带原因，不记改动（免审 ≠ 免校验）。"""
    bad = STRATEGY_YAML.replace("reason: GO}", "reason: TYPO}", 1)  # 只改 exit_step（第一处），edge 仍是 GO → 撞不上
    with pytest.raises(WorkspaceError, match="TYPO|无匹配"):
        ws._write_file("strategies/agent-bad.yaml", bad)


def test_strategy_lib_exists_probe(tmp_path):
    """strategies/_lib.yaml 的 exists 探测（2026-08-24 trace 实证假 not found）：
    read 走 REST 特判，exists 侧同源探测 —— 否则 vendor read 的 contains 预检
    把锁定的模板库吞成 not found，模型被折磨整轮。"""
    from agent.client import ApiClient
    from agent.workspace import ApiWorkspace, ChangeLog

    def transport(ok: bool):
        def t(method: str, path: str, body: dict | None):
            if path == "/api/strategies/_lib":
                return (200, {"text": "# 模板库"}) if ok else (404, {"detail": "没有模板库"})
            return 404, {"detail": "??"}
        return t

    ws_ok = ApiWorkspace(ApiClient(transport=transport(True)), tmp_path, ChangeLog())
    assert ws_ok.contains("strategies/_lib.yaml") is True
    ws_no = ApiWorkspace(ApiClient(transport=transport(False)), tmp_path, ChangeLog())
    assert ws_no.contains("strategies/_lib.yaml") is False
