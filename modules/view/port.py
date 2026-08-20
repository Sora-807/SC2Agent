"""view.port：RecordingPort —— op 流水记录 + origin 打标。

为什么用**装饰器**而不是给 `Operation` 加字段：
1. DSL 计划的不做清单明确禁止改 `Operation` 形态；
2. "谁发的"本来就是**调用方**的属性，不是命令自身的属性 —— 同一条 `move_to` 由 flow 发还是
   用户发，命令内容一模一样，区别只在调用点。装饰器天然知道调用点。

用法：一个环形缓冲 + 多个装饰器（每个运行时一个 origin），它们共享同一条流水：

    ring = OpRing()
    engine = FlowEngine(..., port=RecordingPort(real_port, "flow", ring))
    runtime = ProductionRuntime(..., port=RecordingPort(real_port, "production", ring))
"""
from __future__ import annotations

from collections import deque
from collections.abc import Callable

#: 合法 origin（契约 §2.3 OpsFrame.origin 的闭集）
ORIGINS = ("flow", "production", "worker", "user", "agent")

DEFAULT_RING = 200


class OpRing:
    """op 流水环形缓冲。完整历史属于事件日志（ADR-0024）；这里只留最近一段给 UI。"""

    def __init__(self, maxlen: int = DEFAULT_RING) -> None:
        self._items: deque = deque(maxlen=maxlen)

    def record(self, op, origin: str, at: float | None) -> dict:
        rec = {
            "op_id": op.op_id,
            "seq": op.seq,
            "at": at,
            "action": op.action,
            "unit_tags": list(op.unit_tags),
            "params": dict(op.params),
            "origin": origin,
            # apply 由 submit 的返回值回填；landing 要 D7 GameEvent 才有（B9）
            "apply": None,
            "landing": None,
        }
        self._items.append(rec)
        return rec

    def all(self) -> list[dict]:
        return list(self._items)


class RecordingPort:
    """包一层 port：先记流水，再转发；并把 `ApplyResult` 回填到对应 op。"""

    def __init__(
        self,
        inner,
        origin: str,
        ring: OpRing,
        *,
        clock: Callable[[], float] | None = None,
    ) -> None:
        if origin not in ORIGINS:
            raise ValueError(f"未知 origin {origin!r}（闭集 {ORIGINS}）")
        self._inner = inner
        self._origin = origin
        self._ring = ring
        self._clock = clock

    def submit_operations(self, ops):
        at = None if self._clock is None else self._clock()
        recs = [self._ring.record(op, self._origin, at) for op in ops]
        result = self._inner.submit_operations(ops)
        self._fill_apply(recs, result)
        return result

    def _fill_apply(self, recs: list[dict], result) -> None:
        """按 `ApplyResult` 回填每条 op 的 apply。

        D6 之前 `failed_op_ids` 无人填，所以这里只能给出"整批 ok / 本条在失败名单里"，
        `detail` 一律 None —— 不编原因（不静默）。B9 补齐 ApplyResult 后 detail 才有内容。
        """
        if result is None:
            return
        ok = bool(getattr(result, "ok", True))
        failed_ids = set(getattr(result, "failed_op_ids", ()) or ())
        for rec in recs:
            failed = rec["op_id"] in failed_ids
            rec["apply"] = {"ok": ok and not failed, "failed": failed, "detail": None}

    # 透传 GamePort 其余方法（duck-typing：只有 submit_operations 需要拦）
    def __getattr__(self, name):
        return getattr(self._inner, name)
