"""eval.fixture：入口 fixture（PLAN §3.2）。

OfflineSessionFixture = tests/agent/test_round.py 的 api fixture 模式抽成可复用件：
in-process create_app + 离线 session（autotick=false，setup 手动 tick 造局面）+ 全新
种子工作区（D16：工作区模板也算提示词，每 run 临时目录 + seed_memory_workspace 只补缺失）。
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable

from fastapi.testclient import TestClient

from agent.client import ApiClient
from agent.memory_seed import seed_memory_workspace
from api.app import create_app
from eval.result import _seed_fingerprint


def bridge_client(app_client: TestClient) -> ApiClient:
    """TestClient → agent 的 ApiClient（tests/agent/test_round.py 同款桥，直连 ASGI 不打网络）。"""
    def transport(method: str, path: str, body: dict | None):
        res = (app_client.get(path) if method == "GET"
               else app_client.post(path, json=body or {}))
        try:
            return res.status_code, res.json()
        except ValueError:
            return res.status_code, {"detail": res.text}
    return ApiClient(transport=transport)


class OfflineSessionFixture:
    """离线会话局面（轻管线用，不启 serve_api、不启 SC2）。

    setup_fn(client) 在 session/start 之后跑 —— 造局面（tick 到指定状态、入队、
    写规划文件……），等价 PLAN §3.2 的 ScenarioBuilder 最小版。
    """

    def __init__(self, setup_fn: Callable[[TestClient], None] | None = None) -> None:
        self.setup_fn = setup_fn

    def setup(self, tmp: Path) -> dict:
        app = create_app(tmp / "frames", tmp / "proposals.jsonl")
        client = TestClient(app)
        client.post("/api/session/start", params={"autotick": "false"})
        if self.setup_fn is not None:
            self.setup_fn(client)
        workspace = tmp / "ws"
        workspace.mkdir(parents=True, exist_ok=True)
        seed_memory_workspace(workspace)
        sess = app.state.session
        return {
            "app": app,
            "client": client,
            "api": bridge_client(client),
            "workspace": workspace,
            # D16：种子指纹在 run 前定死（工作区随后会被 agent 写动）
            "seed_hash": _seed_fingerprint(workspace),
            "session": None if sess is None else {
                "seq": getattr(sess, "seq", None), "game_time": sess.game_time},
            "extras": {},
        }

    def teardown(self, world: dict) -> None:
        # in-process app 无常驻资源；显式留给重管线 fixture 覆盖（SC2 会话收尾在那）
        return None
