"""Agent · tools:workspace 之上的复用工具闭包(dispatch/status/wait/done)。"""
from .dispatch import make_dispatch_tool, make_done_tool, make_status_tool, make_wait_tool

__all__ = [
    "make_dispatch_tool",
    "make_status_tool",
    "make_wait_tool",
    "make_done_tool",
]
