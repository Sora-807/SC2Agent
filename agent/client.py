"""agent.client：我们 api 的薄客户端。

刻意用标准库 `urllib`：agent 侧多一个 http 库就多一处版本纠缠，而这里的需求只有
"发几个 JSON 请求"。真要并发再说。

**所有写操作都带 `based_on_seq`**（R8）：值取自观察包，而不是"现在几点"——
agent 拿的是它**看过的那一刻**的版本号，看旧了就该被拒。
"""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

DEFAULT_BASE = "http://127.0.0.1:8770"


class ApiError(Exception):
    """带上 HTTP 状态与后端给的原因 —— agent 要能区分"世界变了"和"请求不合法"。"""

    def __init__(self, status: int, detail: Any) -> None:
        super().__init__(f"HTTP {status}: {detail}")
        self.status = status
        self.detail = detail

    @property
    def stale(self) -> bool:
        """409 + current_seq = 观察过期（重取观察再试），不是请求写错了。"""
        return self.status == 409 and isinstance(self.detail, dict) and "current_seq" in self.detail


#: 传输接缝：`(method, path, body) -> (status, json)`。
#: 默认走 urllib；测试注入一个直连 ASGI 的实现，就不用为每个测试起真服务
#: （起服务的测试慢、还会和开发中的那个服务抢端口）。
Transport = Callable[[str, str, "dict | None"], "tuple[int, Any]"]


@dataclass
class ApiClient:
    base: str = DEFAULT_BASE
    timeout: float = 20.0
    transport: Transport | None = field(default=None)

    # ---- 读 ----

    def observation(self, *, source: str = "live", text: bool = True) -> dict:
        return self._get(f"/api/observation?source={source}&text={'true' if text else 'false'}")

    def agent_tools(self) -> dict:
        return self._get("/api/agent/tools")

    def proposals(self) -> list[dict]:
        return self._get("/api/proposals")

    def session(self) -> dict:
        return self._get("/api/session")

    def latest_frame(self, topic: str, *, source: str = "live") -> dict | None:
        """某 topic 的最新一帧 payload（游标拉满取尾）。F 批体检用（frame/production）。"""
        frames = self._get(f"/api/sources/{source}/frames?game_time=999999")
        for f in reversed(frames or []):
            if isinstance(f, dict) and f.get("topic") == topic:
                return f.get("payload") or {}
        return None

    # ---- 写（只有提案；命令类刻意不暴露给 agent，见模块 docstring）----

    def propose(self, body: dict) -> dict:
        return self._post("/api/proposals", body)

    # ---- 规划域（P3）：离线规划文件直改 —— 用户 2026-08-21 拍板 codeagent 语义，
    # 不走提案（diff/撤销兜底在文件层）。live 对局状态仍然只有 propose 一条路，
    # 「不能直改」的边界收窄为「不能直改对局状态」，规划文件是 authoring 数据。

    def plans_list(self) -> list[dict]:
        return self._get("/api/plans")

    def plan_get(self, pid: str) -> dict:
        return self._get(f"/api/plans/{pid}")

    def plan_create(self, body: dict) -> dict:
        return self._post("/api/plans", body)

    def plan_save(self, pid: str, body: dict) -> dict:
        return self._put(f"/api/plans/{pid}", body)

    def plans_simulate(self, body: dict) -> dict:
        return self._post("/api/plans/simulate", body)

    def map_plans_list(self) -> list[dict]:
        return self._get("/api/map-plans")

    def map_plan_payload(self, pid: str) -> dict:
        return self._get(f"/api/map-plans/{pid}")

    def initial_states_list(self) -> list[dict]:
        return self._get("/api/initial-states")

    def initial_state_get(self, pid: str) -> dict:
        return self._get(f"/api/initial-states/{pid}")

    def initial_state_save(self, pid: str, doc: dict) -> dict:
        return self._put(f"/api/initial-states/{pid}", doc)

    def session_export(self, save_as: str | None = None) -> dict:
        path = "/api/session/export" + (f"?id={save_as}" if save_as else "")
        return self._get(path)

    def map_plan_doc(self, pid: str) -> dict:
        """地图规划的**文档形状**（id/title/spawn/build_slots/pos_marks）—— 文件视图的读写体。"""
        return self._get(f"/api/map-plans/{pid}/doc")

    def map_plan_save_payload(self, pid: str, doc: dict) -> dict:
        """全量保存地图规划文档（服务端做重叠/压预留区校验）。"""
        return self._put(f"/api/map-plans/{pid}/doc", doc)

    def map_plan_create(self, body: dict) -> dict:
        return self._post("/api/map-plans", body)

    def map_plan_save(self, pid: str, hunks: list[dict]) -> dict:
        return self._put(f"/api/map-plans/{pid}", {"hunks": hunks})

    def session_start(self, *, driver: str = "sim", map_plan: str | None = None,
                      strategy: str | None = None, loadout: str | None = None,
                      spawn: str | None = None, mode: str | None = None,
                      speed: float | None = None, autotick: bool = True,
                      production: dict | None = None) -> dict:
        q = f"driver={driver}&autotick={'true' if autotick else 'false'}"
        if map_plan:
            q += f"&map_plan={urllib.parse.quote(map_plan)}"
        if strategy:
            q += f"&strategy={urllib.parse.quote(strategy)}"
        if loadout:
            q += f"&loadout={urllib.parse.quote(loadout)}"
        if spawn:
            q += f"&spawn={urllib.parse.quote(spawn)}"
        if mode:
            q += f"&mode={urllib.parse.quote(mode)}"
        if speed is not None:
            q += f"&speed={float(speed):g}"
        # 结构化参数走请求体（query 放不下 dict；§0.52 C 批）
        return self._post(f"/api/session/start?{q}",
                          {"production": production} if production else {})

    def session_stop(self) -> dict:
        """结束当前会话（子进程树杀，防孤儿 SC2）。"""
        return self._post("/api/session/stop", {})

    def strategies_list(self) -> list[dict]:
        return self._get("/api/strategies")

    def strategy_lib_text(self) -> str:
        """模板库 `_lib.yaml` 原文（ADR-0031；只读，锁定文件没有写面）。"""
        r = self._get("/api/strategies/_lib")
        return r.get("text") if isinstance(r, dict) else str(r)

    def strategy_doc(self, sid: str) -> dict:
        """策略文件的**文档形状**（strategy + assembly 两段）—— 文件视图的读写体。"""
        return self._get(f"/api/strategies/{sid}/doc")

    def strategy_save_payload(self, sid: str, doc: dict) -> dict:
        """全量保存策略文档（服务端 parse/validate 全套编译期校验）。"""
        return self._put(f"/api/strategies/{sid}/doc", doc)

    def strategy_create(self, body: dict) -> dict:
        return self._post("/api/strategies", body)

    def notes_list(self) -> list[dict]:
        return self._get("/api/agent/notes")

    def note_save(self, text: str, title_zh: str | None = None) -> dict:
        body: dict = {"text": text}
        if title_zh:
            body["title_zh"] = title_zh
        return self._post("/api/agent/notes", body)

    def _get(self, path: str) -> Any:
        return self._call(path, None, "GET")

    def _post(self, path: str, body: dict) -> dict:
        return self._call(path, body, "POST")

    def _put(self, path: str, body: dict) -> dict:
        return self._call(path, body, "PUT")

    def _call(self, path: str, body: dict | None, method: str) -> Any:
        if self.transport is not None:
            status, payload = self.transport(method, path, body)
            if status >= 400:
                detail = payload.get("detail", payload) if isinstance(payload, dict) else payload
                raise ApiError(status, detail)
            return payload
        req = urllib.request.Request(
            self.base + path,
            data=None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={"content-type": "application/json"},
            method=method,
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return json.load(resp)
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", "replace")
            try:
                detail = json.loads(raw).get("detail", raw)
            except ValueError:
                detail = raw
            raise ApiError(exc.code, detail) from None
        except urllib.error.URLError as exc:
            raise ApiError(0, f"连不上 {self.base}：{exc.reason}") from None