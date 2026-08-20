"""运行时 trace 基础设施:事件流 + 全量对话 + checkpoint + summary + HTML 可视化。"""
from . import events  # noqa: F401
from .render import load_trace, render_trace_html
from .replay import ReplayResult, replay_trace, replay_workspace
from .tracer import Tracer

__all__ = [
    "Tracer",
    "load_trace",
    "render_trace_html",
    "replay_workspace",
    "replay_trace",
    "ReplayResult",
    "events",
]
