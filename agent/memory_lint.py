"""agent.memory_lint：memory/*.md 的写钩子软提示（A4，2026-08-23）。

规则只有两条（写在提示词里的同一套约定，见 agent/spec.py「记忆」节）：
1. 条目要带 `[ID]`（E 系经验 / S 系偏好）—— 没 ID 的条目 grep 不到、改旧条时对不上号；
2. `memory/strategy-notes.md` 的条目要带状态字段（实测 / 未验证 / 词表已核实）——
   没有状态的"经验"会误导后来的会话把猜测当结论用。

**软提示不拒绝**：lint 结果附在写结果后面，写入照常（I19 的记忆是 agent 自己的地盘，
规则靠提示词 + 反馈回路养成，不是靠校验器挡）。纯函数、无 IO —— 测试锁规则表本身。
"""
from __future__ import annotations

import re

#: 条目判定的行形态：Markdown 列表项（- / * / 数字.）或以 [E1]/[S2] 开头的行
_ENTRY_LINE = re.compile(r"^\s*(?:[-*]\s+|\d+[.、]\s+)")
#: 条目 ID：[E12] / [S3]（E 系经验 / S 系偏好/拍板）
_ID_TAG = re.compile(r"\[[ES]\d+\]")
#: strategy-notes 的状态字段（提示词里约定的三态）
_STATUS_WORDS = ("实测", "未验证", "词表已核实")
#: 提示条数上限：lint 是软提示，刷屏就成噪声了
_MAX_HINTS = 5


def lint_memory(path: str, content: str) -> list[str]:
    """`memory/<name>.md` 的内容 → 提示列表（空 = 没问题）。

    只对 `memory/` 前缀 + `.md` 后缀的路径生效；条目 = 列表项行。
    """
    if not path.startswith("memory/") or not path.endswith(".md"):
        return []
    hints: list[str] = []
    is_notes = path == "memory/strategy-notes.md"
    entries: list[tuple[int, str]] = []   # (行号从 1 起, 行文本)
    for no, line in enumerate(content.splitlines(), 1):
        if _ENTRY_LINE.match(line):
            entries.append((no, line))
    if not entries:
        return []
    for no, line in entries:
        if not _ID_TAG.search(line):
            hints.append(f"第 {no} 行条目没有 [ID]（[E#] 经验 / [S#] 偏好）——"
                         "没 ID 的条目 grep 不到、改旧条时对不上号")
        if is_notes and not any(w in line for w in _STATUS_WORDS):
            hints.append(f"第 {no} 行经验条目缺状态字段（实测 / 未验证 / 词表已核实）——"
                         "没状态的「经验」会被后来的会话当结论用")
        if len(hints) >= _MAX_HINTS:
            hints.append(f"…（还有更多，先改这几条；规则见 system/surface.md 或提示词「记忆」节）")
            break
    return hints
