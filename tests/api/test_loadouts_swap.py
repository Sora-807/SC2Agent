"""批 B/C（2026-08-23 执行轮，WORKLOG §0.41）：loadout 三件套、队列 insert/replace_head、
in_flight.from_index、热切 V1（offline 续位/重起/409，sim 子进程端到端）。
"""
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from api.app import create_app

from flow.templates import SEED_LIB_PATH as _LIB_SRC


def make_client(tmp_path: Path) -> TestClient:
    """loadout/策略文件都是建 app 时扫描 —— 先写文件、后建 app（每个测试自己建）。"""
    import shutil

    (tmp_path / "strategies").mkdir(parents=True, exist_ok=True)
    (tmp_path / "loadouts").mkdir(parents=True, exist_ok=True)
    dst = tmp_path / "strategies" / "_lib.yaml"
    if not dst.exists():
        shutil.copy(_LIB_SRC, dst)
    return TestClient(create_app(tmp_path / "frames", tmp_path / "p.jsonl",
                                 plans_dir=tmp_path / "plans",
                                 map_plans_dir=tmp_path / "map-plans",
                                 strategies_dir=tmp_path / "strategies",
                                 loadouts_dir=tmp_path / "loadouts"))


@pytest.fixture()
def client(tmp_path: Path) -> TestClient:
    c = make_client(tmp_path)
    yield c
    if c.app.state.session is not None and hasattr(c.app.state.session, "proc"):
        sess = c.app.state.session
        if sess.proc.poll() is None:
            sess.stop()
    c.app.state.proposals.session = None
    c.app.state.session = None


# ---------------- B1：loadout 三件套 ----------------

def _write_loadout(tmp_path: Path, doc: dict) -> None:
    (tmp_path / "loadouts").mkdir(parents=True, exist_ok=True)
    (tmp_path / "loadouts" / f"{doc['id']}.yaml").write_text(
        yaml.safe_dump(doc, allow_unicode=True), encoding="utf-8")


def _write_strategy_file(tmp_path: Path, sid: str, group_slots=("main",)) -> Path:
    doc = {
        "strategy": {
            "id": sid, "version": 1, "group_slots": list(group_slots),
            "params": {"min_units": {"type": "int", "default": 1}},
            "variables": {}, "initial_step": "gather",
            "imports": {"gather": {"from": "_lib", "template": "gather",
                                   "params": {"min_units": 1, "unit_type": "terran/scv"}}},
            "steps": [{"step_id": "finish", "branches": [
                {"do": [{"op": "exit_strategy", "kind": "done", "reason": "DONE"}]}]}],
            "edges": [{"from": "gather", "to": "finish",
                       "kind": "done", "reason": "FORMED"}],
        },
        "assembly": {
            "id": f"{sid}_asm",
            "groups": [{"group_id": "G_MAIN", "composition":
                        {"terran/scv": {"min": 12, "target": 12, "max": 12}}}],
            "strategy_instances": [{"instance_id": "s1", "strategy_ref": sid,
                                    "bindings": {"main": "G_MAIN"}, "params": {}}],
        },
    }
    (tmp_path / "strategies").mkdir(parents=True, exist_ok=True)
    path = tmp_path / "strategies" / f"{sid}.yaml"
    path.write_text(yaml.safe_dump(doc, allow_unicode=True), encoding="utf-8")
    return path


def test_loadout_missing_400_with_listing(client: TestClient):
    r = client.post("/api/session/start",
                    params={"driver": "offline", "loadout": "nope", "autotick": "false"})
    assert r.status_code == 400 and "nope" in r.json()["detail"]


def test_loadout_assembles_three_refs_and_submits_queue(tmp_path: Path):
    """loadout 一发入魂：策略装配 + 生产序列自动 submit 入队（main 队列）。"""
    _write_strategy_file(tmp_path, "scv_walk")
    _write_loadout(tmp_path, {
        "id": "scw", "title_zh": "工人散步", "strategy": "scv_walk", "plan": "default",
    })
    client = make_client(tmp_path)
    r = client.post("/api/session/start",
                    params={"driver": "offline", "loadout": "scw", "autotick": "false"})
    assert r.status_code == 200, r.text
    sess = client.app.state.session
    q = sess.runtime.queue("main")
    assert q is not None and len(q.items) > 0, "loadout 的生产序列应自动入队"
    statics = client.get("/api/sources/live/statics").json()
    strat = next(s["payload"] for s in statics if s["topic"] == "static/strategy")
    assert strat["id"] == "scv_walk" and strat["imported"] == ["gather"]


def test_loadout_bad_plan_ref_400(tmp_path: Path):
    _write_strategy_file(tmp_path, "scv_walk")
    _write_loadout(tmp_path, {"id": "bad", "strategy": "scv_walk", "plan": "ghost"})
    client = make_client(tmp_path)
    r = client.post("/api/session/start",
                    params={"driver": "offline", "loadout": "bad", "autotick": "false"})
    assert r.status_code == 400 and "ghost" in r.json()["detail"]


# ---------------- B2：insert / replace_head ----------------

def _offline(client: TestClient) -> object:
    client.post("/api/session/start", params={"driver": "offline", "autotick": "false"})
    return client.app.state.session


def _cmd(client: TestClient, op: str, body: dict):
    sess = client.app.state.session
    return client.post(f"/api/commands/queue/{op}",
                       json={"based_on_seq": sess.seq, **body})


def _depot():
    return {"op": "build", "type": "terran/supplydepot", "count": 1,
            "placement": {"kind": "in_region", "region": "home"}}


def _scv():
    return {"op": "train", "type": "terran/scv", "count": 1}


def test_insert_positions_in_remaining_queue(client: TestClient):
    sess = _offline(client)
    assert _cmd(client, "submit", {"items": [_depot(), _depot(), _scv()]}).status_code == 200
    assert _cmd(client, "insert", {"index": 1, "items": [_scv()]}).status_code == 200
    q = sess.runtime.queue("main")
    assert q.items[1].op.value == "train", "index=1 插在剩余队列位置 1（下一执行的第二项）"


def test_insert_out_of_range_400(client: TestClient):
    _offline(client)
    assert _cmd(client, "submit", {"items": [_depot()]}).status_code == 200
    r = _cmd(client, "insert", {"index": 5, "items": [_scv()]})
    assert r.status_code == 400 and "index" in r.json()["detail"]


def test_insert_at_zero_is_next_to_execute(client: TestClient):
    sess = _offline(client)
    assert _cmd(client, "submit", {"items": [_depot(), _depot()]}).status_code == 200
    assert _cmd(client, "insert", {"index": 0, "items": [_scv()]}).status_code == 200
    assert sess.runtime.queue("main").items[0].op.value == "train"


def test_replace_head_is_atomic(client: TestClient):
    sess = _offline(client)
    assert _cmd(client, "submit", {"items": [_depot(), _scv(), _scv()]}).status_code == 200
    r = _cmd(client, "replace_head", {"items": [_scv(), _scv()]})
    assert r.status_code == 200, r.text
    ops = [i.op.value for i in sess.runtime.queue("main").items]
    # 3 项 - 换掉队首 1 项 + 换上 2 项 = 4 项，全部 train，顺序即换上的在前
    assert ops == ["train"] * 4, "队首被原子换掉，其余不动"


def test_replace_head_on_empty_queue_prepends(client: TestClient):
    sess = _offline(client)
    assert _cmd(client, "replace_head", {"items": [_depot()]}).status_code == 200
    assert len(sess.runtime.queue("main").items) == 1


def test_replace_head_requires_items(client: TestClient):
    _offline(client)
    r = _cmd(client, "replace_head", {"items": []})
    assert r.status_code == 400 and "items" in r.json()["detail"]


def test_live_queue_ops_shape_matches_offline(client: TestClient):
    """B7：live 与 offline 的命令返回同键（items / accepted_seq），不再各说各话。"""
    client.post("/api/session/start", params={"driver": "sim", "autotick": "false"})
    sess = client.app.state.session
    r = client.post("/api/commands/queue/append",
                    json={"based_on_seq": sess.seq, "items": [_scv()]})
    assert r.status_code == 200
    detail = r.json()["detail"]
    assert detail["items"] == 1 and "accepted_seq" in detail
    assert "dispatched" not in detail
    client.post("/api/session/stop")


# ---------------- B3：in_flight.from_index ----------------

def test_in_flight_carries_from_index(client: TestClient):
    """emit 时记来源队列序号（B3）。语义 = **发令那一刻的剩余队列下标**：
    同帧先出队的项（train）不在队列里，后面项的下标跟着前移 —— 如实记录，不回补。"""
    sess = _offline(client)
    # 队首 count=2 的 depot：第一帧发一次（count 2→1 留队）→ 第二项同帧在 i=1 发令
    assert _cmd(client, "submit", {
        "items": [{**_depot(), "count": 2}, _depot()]}).status_code == 200
    sess.tick()
    snap = sess.runtime.snapshot()
    flights = [f for f in snap["in_flight"] if f["stable_id"] == "terran/supplydepot"]
    assert len(flights) == 2, "count=2 留队 + 第二项，同帧两条在途"
    assert flights[0]["from_index"] == 0     # 发令时它是队首
    assert flights[1]["from_index"] == 1     # count>1 留队 → 第二项在剩余队列下标 1


# ---------------- 批 C：热切 V1 ----------------

STAY_DOC = {
    "strategy": {
        "id": "stay", "version": 1, "group_slots": ["main"],
        "params": {}, "variables": {"pushes": {"type": "int", "default": 0}},
        "initial_step": "gather",
        # min=99：沙盒 12 工永远凑不齐 → 停在 gather（给续位测试一个稳定的"当前 step"）
        "imports": {"gather": {"from": "_lib", "template": "gather",
                               "params": {"min_units": 99, "unit_type": "terran/scv"}}},
        "steps": [{"step_id": "finish", "branches": [
            {"do": [{"op": "exit_strategy", "kind": "done", "reason": "DONE"}]}]}],
        "edges": [{"from": "gather", "to": "finish", "kind": "done", "reason": "FORMED"}],
    },
    "assembly": {
        "id": "stay_asm",
        "groups": [{"group_id": "G_MAIN", "composition":
                    {"terran/scv": {"min": 12, "target": 12, "max": 12}}}],
        "strategy_instances": [{"instance_id": "s1", "strategy_ref": "stay",
                                "bindings": {"main": "G_MAIN"}, "params": {}}],
    },
}

# 换到同名 step（gather）→ 续位；locals/timers 保留由 engine 级测试锁，这里锁 API 语义
SWAP_DOC_SAME_STEP = {
    "strategy": {
        "id": "stay_v2", "version": 2, "group_slots": ["main"],
        "params": {}, "variables": {"pushes": {"type": "int", "default": 0}},
        "initial_step": "gather",
        "imports": {"gather": {"from": "_lib", "template": "gather",
                               "params": {"min_units": 99, "unit_type": "terran/scv"}}},
        "steps": [{"step_id": "finish", "branches": [
            {"do": [{"op": "exit_strategy", "kind": "done", "reason": "DONE"}]}]}],
        "edges": [{"from": "gather", "to": "finish", "kind": "done", "reason": "FORMED"}],
    },
    "assembly": {
        "id": "stay_v2_asm",
        "groups": [{"group_id": "G_MAIN", "composition":
                    {"terran/scv": {"min": 12, "target": 12, "max": 12}}}],
        "strategy_instances": [{"instance_id": "s1", "strategy_ref": "stay_v2",
                                "bindings": {"main": "G_MAIN"}, "params": {}}],
    },
}

# 不含当前 step（gather）→ 从 initial_step（wait2）重起
SWAP_DOC_RESTART = {
    "strategy": {
        "id": "fresh", "version": 1, "group_slots": ["main"],
        "params": {}, "variables": {},
        "initial_step": "wait2",
        "steps": [
            {"step_id": "wait2", "branches": [
                # 纯等待 step（无 exit、无出边）：热切后停在这里观察，不会自己结束
                {"do": []}]},
        ],
        "edges": [],
    },
    "assembly": {
        "id": "fresh_asm",
        "groups": [{"group_id": "G_MAIN", "composition":
                    {"terran/scv": {"min": 12, "target": 12, "max": 12}}}],
        "strategy_instances": [{"instance_id": "s1", "strategy_ref": "fresh",
                                "bindings": {"main": "G_MAIN"}, "params": {}}],
    },
}

# group_slots 不一致 → 409
SWAP_DOC_BAD_SLOTS = {
    "strategy": {
        "id": "bad_slots", "version": 1, "group_slots": ["main", "scout"],
        "params": {}, "variables": {}, "initial_step": "w",
        "steps": [{"step_id": "w", "branches": [{"do": []}]}],
        "edges": [],
    },
    "assembly": {
        "id": "bad_asm",
        "groups": [
            {"group_id": "G_MAIN", "composition":
             {"terran/scv": {"min": 12, "target": 12, "max": 12}}},
            {"group_id": "G_SCOUT", "composition":
             {"terran/marine": {"min": 1, "target": 4, "max": 4}}}],
        "strategy_instances": [{"instance_id": "s1", "strategy_ref": "bad_slots",
                                "bindings": {"main": "G_MAIN", "scout": "G_SCOUT"},
                                "params": {}}],
    },
}


def _save_strategy_via_api(client: TestClient, sid: str, doc: dict) -> None:
    client.post("/api/strategies", json={"id": sid})
    r = client.put(f"/api/strategies/{sid}/doc", json=doc)
    assert r.json()["ok"], r.text


def test_swap_same_step_stays_put(client: TestClient, tmp_path: Path):
    _save_strategy_via_api(client, "stay", STAY_DOC)
    _save_strategy_via_api(client, "stay_v2", SWAP_DOC_SAME_STEP)
    client.post("/api/session/start",
                params={"driver": "offline", "strategy": "stay", "autotick": "false"})
    sess = client.app.state.session
    sess.tick()
    assert sess.engine.snapshot()["active_step"] == "gather"   # min=99：一直停在 gather
    r = client.post("/api/session/swap", params={"strategy": "stay_v2"})
    assert r.status_code == 200, r.text
    sess.tick()          # 帧边界应用
    snap = sess.engine.snapshot()
    assert snap["strategy_ref"] == "stay_v2"
    assert snap["active_step"] == "gather"  # 新策略含同名 gather → 续位停留
    assert snap["done"] is False
    tr = snap["transitions"][-1]
    assert tr["kind"] == "swap" and "stay@1→stay_v2@2" in tr["reason"] and "续位" in tr["reason"]


def test_swap_revives_finished_strategy(client: TestClient):
    """对已结束的策略 swap = 复活（done/exit_record 清零，从新策略起跑）。"""
    done_doc = {
        "strategy": {**STAY_DOC["strategy"], "id": "done_once",
                     "imports": {"gather": {"from": "_lib", "template": "gather",
                                            "params": {"min_units": 1,
                                                       "unit_type": "terran/scv"}}}},
        "assembly": {**STAY_DOC["assembly"], "id": "done_once_asm",
                     "strategy_instances": [{**STAY_DOC["assembly"]["strategy_instances"][0],
                                             "strategy_ref": "done_once"}]},
    }
    _save_strategy_via_api(client, "done_once", done_doc)
    _save_strategy_via_api(client, "fresh", SWAP_DOC_RESTART)
    client.post("/api/session/start",
                params={"driver": "offline", "strategy": "done_once", "autotick": "false"})
    sess = client.app.state.session
    sess.tick(); sess.tick()
    assert sess.engine.snapshot()["done"] is True
    client.post("/api/session/swap", params={"strategy": "fresh"})
    sess.tick()
    snap = sess.engine.snapshot()
    assert snap["done"] is False and snap["active_step"] == "wait2"


def test_swap_missing_step_restarts(client: TestClient):
    _save_strategy_via_api(client, "stay", STAY_DOC)
    _save_strategy_via_api(client, "fresh", SWAP_DOC_RESTART)
    client.post("/api/session/start",
                params={"driver": "offline", "strategy": "stay", "autotick": "false"})
    sess = client.app.state.session
    sess.tick()
    r = client.post("/api/session/swap", params={"strategy": "fresh"})
    assert r.status_code == 200, r.text
    sess.tick()
    snap = sess.engine.snapshot()
    assert snap["strategy_ref"] == "fresh"
    assert snap["active_step"] == "wait2"   # 不含 gather → 从 initial_step 重起
    assert "重起" in snap["transitions"][-1]["reason"]


def test_swap_group_slots_mismatch_409(client: TestClient):
    _save_strategy_via_api(client, "stay", STAY_DOC)
    _save_strategy_via_api(client, "bad_slots", SWAP_DOC_BAD_SLOTS)
    client.post("/api/session/start",
                params={"driver": "offline", "strategy": "stay", "autotick": "false"})
    sess = client.app.session = client.app.state.session
    before = sess.engine.snapshot()["strategy_ref"]
    r = client.post("/api/session/swap", params={"strategy": "bad_slots"})
    assert r.status_code == 409 and "group_slots" in r.json()["detail"]
    assert sess.engine.snapshot()["strategy_ref"] == before, "会话不受影响"


def test_swap_static_strategy_frame_refreshed(client: TestClient):
    """swap 后 static/strategy 重发（事件式静态面）—— UI 的策略图跟着换。"""
    _save_strategy_via_api(client, "stay", STAY_DOC)
    _save_strategy_via_api(client, "stay_v2", SWAP_DOC_SAME_STEP)
    client.post("/api/session/start",
                params={"driver": "offline", "strategy": "stay", "autotick": "false"})
    sess = client.app.state.session
    sess.tick()
    client.post("/api/session/swap", params={"strategy": "stay_v2"})
    sess.tick()
    latest = sess.latest_at(sess.game_time, {"static/strategy"})
    assert latest[0]["payload"]["id"] == "stay_v2"


def test_swap_on_sim_subprocess_end_to_end(client: TestClient, tmp_path: Path):
    """live 通道（stdin）端到端：swap 命令进子进程，转移历史与 static/strategy 都换。"""
    _save_strategy_via_api(client, "stay", STAY_DOC)
    _save_strategy_via_api(client, "fresh", SWAP_DOC_RESTART)
    r = client.post("/api/session/start",
                    params={"driver": "sim", "strategy": "stay", "autotick": "false"})
    assert r.status_code == 200, r.text
    import time as _t

    deadline = _t.time() + 15
    while _t.time() < deadline:
        if client.get("/api/session").json().get("seq", 0) > 0:
            break
        _t.sleep(0.1)
    r = client.post("/api/session/swap", params={"strategy": "fresh"})
    assert r.status_code == 200, r.text
    sess = client.app.state.session
    deadline = _t.time() + 15
    swapped = False
    while _t.time() < deadline:
        frames = sess.latest_at(sess.game_time)
        flow = next((f["payload"] for f in frames if f["topic"] == "frame/flow"), None)
        if flow and flow["strategies"][0]["strategy_ref"] == "fresh":
            swapped = True
            break
        _t.sleep(0.1)
    assert swapped, "sim 子进程应已热切到 fresh"
    statics = [f for f in sess.latest_at(sess.game_time, {"static/strategy"})]
    assert statics[-1]["payload"]["id"] == "fresh"
    client.post("/api/session/stop")


def test_sc2_control_file_channel(tmp_path: Path):
    """sc2 没有 stdin（DEVNULL）：命令走控制文件 —— rename→读→删 的无损协议。"""
    import subprocess
    import sys

    ctl = tmp_path / "ctl.json"
    proc = subprocess.Popen(
        [sys.executable, "-X", "utf8", str(Path("tools/run_session.py")),
         "--driver", "sim", "--seconds", "2", "--tick-seconds", "0",
         "--control-file", str(ctl)],
        cwd=str(Path(__file__).resolve().parents[2]),
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True, encoding="utf-8",
    )
    # 等子进程起来，写入一条控制命令（stdout 有第一批帧）
    import time as _t

    deadline = _t.time() + 10
    got_frame = False
    while _t.time() < deadline and not got_frame:
        if ctl.exists() or proc.poll() is not None:
            break
        try:
            line = proc.stdout.readline()
            got_frame = line.startswith('{"topic"')
        except (OSError, ValueError):
            pass
    ctl.write_text('{"op": "queue", "kind": "append", "name": "main",'
                   ' "items": [{"op": "train", "type": "terran/scv", "count": 1}],'
                   ' "index": null, "order": null}\n', encoding="utf-8")
    out, _err = proc.communicate(timeout=20)
    assert '"_": "ack"' in out or '"op":"queue"' in out or '"ack"' in out.replace(" ", ""), \
        "控制文件命令应被确认（ack 控制行）"
    assert not ctl.exists(), "控制文件消费后应删除"


# ---------------- 开局生产力默认值（§0.52 C 批）----------------

def test_start_production_sets_worker_quotas(client: TestClient):
    """production 请求体 → 会话建立即下发采集配额（目标值语义，offline 也能配）。"""
    r = client.post("/api/session/start?driver=offline&autotick=false",
                    json={"production": {"mineral_workers": 8, "gas_workers": 3}})
    assert r.status_code == 200, r.text
    keeper = client.app.state.session.keeper
    assert keeper.policy.mineral_workers == 8
    assert keeper.policy.gas_workers == 3


def test_start_production_accepts_short_keys_and_idle(client: TestClient):
    r = client.post("/api/session/start?driver=offline&autotick=false",
                    json={"production": {"mineral": 10, "idle": 1}})
    assert r.status_code == 200, r.text
    keeper = client.app.state.session.keeper
    assert keeper.policy.mineral_workers == 10
    assert keeper.policy.reserve_idle == 1


def test_start_production_rejects_bad_keys_and_values(client: TestClient):
    """坏参数在建会话**之前**就 400 —— 不留一个半配置的会话。"""
    r1 = client.post("/api/session/start?driver=offline&autotick=false",
                     json={"production": {"vespene_workers": 3}})
    assert r1.status_code == 400 and "vespene_workers" in r1.text
    r2 = client.post("/api/session/start?driver=offline&autotick=false",
                     json={"production": {"mineral_workers": -1}})
    assert r2.status_code == 400
    assert client.app.state.session is None, "校验失败不建会话"
