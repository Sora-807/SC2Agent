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

    - setup_fn(client)：session/start **之后**跑 —— 造对局局面（tick、入队、
      提高配额……），拿 TestClient 直接操作。
    - prepare(tmp)：建 app **之前**跑 —— 写预置文件（规划/策略/地图规划）。
      约定式挂载：prepare 写出的 tmp/plans、tmp/strategies、tmp/map-plans、
      tmp/loadouts 目录存在哪个，就自动接进 create_app 对应参数 —— 场景侧
      不用碰 app 装配细节（「轻松注册」的入口半边）。
    """

    def __init__(self, setup_fn: Callable[[TestClient], None] | None = None,
                 prepare: Callable[[Path], None] | None = None) -> None:
        self.setup_fn = setup_fn
        self.prepare = prepare

    def setup(self, tmp: Path) -> dict:
        if self.prepare is not None:
            self.prepare(tmp)
        app = create_app(self._or_none(tmp / "frames"), tmp / "proposals.jsonl",
                         plans_dir=self._or_none(tmp / "plans"),
                         map_plans_dir=self._or_none(tmp / "map-plans"),
                         strategies_dir=self._or_none(tmp / "strategies"),
                         loadouts_dir=self._or_none(tmp / "loadouts"))
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

    @staticmethod
    def _or_none(path: Path) -> Path | None:
        """约定式挂载：prepare 写出来的目录才接进 app（没写 = None = 内存态默认）。"""
        return path if path.is_dir() else None

    def teardown(self, world: dict) -> None:
        # in-process app 无常驻资源；显式留给重管线 fixture 覆盖（SC2 会话收尾在那）
        return None
