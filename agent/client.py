"""agent.client：我们 api 的薄客户端。

刻意用标准库 `urllib`：agent 侧多一个 http 库就多一处版本纠缠，而这里的需求只有
"发几个 JSON 请求"。真要并发再说。

**所有写操作都带 `based_on_seq`**（R8）：值取自观察包，而不是"现在几点"——
agent 拿的是它**看过的那一刻**的版本号，看旧了就该被拒。
"""
from __future__ import annotations

import json
import urllib.error
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

    # ---- 写（只有提案；命令类刻意不暴露给 agent，见模块 docstring）----

    def propose(self, body: dict) -> dict:
        return self._post("/api/proposals", body)

    def _get(self, path: str) -> Any:
        return self._call(path, None, "GET")

    def _post(self, path: str, body: dict) -> Any:
        return self._call(path, body, "POST")

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