"""view.fmt：展示层格式化小函数（alerts/observe 共用的文本格式）。"""
from __future__ import annotations


def mmss(t: float) -> str:
    """游戏秒 → `MM:SS`（负值钳 0；时间轴/警报文案用）。"""
    s = max(0, int(round(t)))
    return f"{s // 60:02d}:{s % 60:02d}"
