"""memory lint（A4）：规则表纯函数 + 写钩子软提示（不拒绝）。

规则与提示词「记忆」节同一套：条目带 [ID]（E/S 系）、strategy-notes 条目带
状态字段（实测/未验证/词表已核实）。lint 是软提示 —— 写入照常，提示附在写结果上。
"""
from __future__ import annotations

from pathlib import Path

import pytest
from agentic.workspace.workspace import WorkspaceError

from agent.memory_lint import lint_memory


def test_non_memory_paths_are_ignored():
    assert lint_memory("plans/x.yaml", "- 没有ID的行") == []
    assert lint_memory("analysis.md", "- 没有ID的行") == []


def test_entry_without_id_gets_hint():
    hints = lint_memory("memory/user-preferences.md", "# 偏好\n- 用户喜欢速攻\n")
    assert len(hints) == 1 and "[ID]" in hints[0]


def test_entry_with_id_no_hint():
    assert lint_memory("memory/user-preferences.md", "- [S1] 用户喜欢速攻\n") == []


def test_strategy_notes_also_requires_status_field():
    hints = lint_memory("memory/strategy-notes.md", "- [E1] 堵口好用\n")
    assert len(hints) == 1 and "状态字段" in hints[0]
    assert lint_memory("memory/strategy-notes.md", "- [E1] 堵口好用（实测）\n") == []
    assert lint_memory("memory/strategy-notes.md", "- [E2] 蛙跳距离待定（未验证）\n") == []
    assert lint_memory("memory/strategy-notes.md", "- [E3] move_to 签名（词表已核实）\n") == []


def test_prose_lines_are_not_entries():
    """标题与散文行不是条目 —— lint 只看列表项，别把整篇文档都点名。"""
    assert lint_memory("memory/strategy-notes.md", "# 经验\n一段说明文字，没有列表项。\n") == []


def test_hints_are_capped():
    body = "".join(f"- 第{i}条没ID\n" for i in range(20))
    hints = lint_memory("memory/strategy-notes.md", body)
    # 封顶 = 5 条提示 + 1 行省略（条目的 ID/状态两提示成对入列，所以最多 6+1）
    assert 5 <= len(hints) <= 7 and hints[-1].startswith("…")


def test_write_hook_records_hints_without_rejecting(tmp_path: Path):
    """写入照常 + 提示进队列（lint 版文件工具从这里取）。"""
    from agent.client import ApiClient
    from agent.workspace import ApiWorkspace, ChangeLog

    ws = ApiWorkspace(ApiClient(), tmp_path, ChangeLog())
    ws.write_text("memory/strategy-notes.md", "- [E1] 没状态的条目\n")
    hints = ws.drain_lint_hints()
    assert any("状态字段" in h for h in hints)
    assert ws.read_text("memory/strategy-notes.md") == "- [E1] 没状态的条目\n"
    assert ws.drain_lint_hints() == []          # 取过就清


def test_lib_file_is_readable_but_not_writable(tmp_path: Path):
    """`_` 前缀锁定：strategies/_lib.yaml 可读（经 REST 只读端点）、写被指路拒绝。"""
    from agent.client import ApiClient, ApiError
    from agent.workspace import ApiWorkspace, ChangeLog

    class _FakeTransport:
        def __call__(self, method, path, body):
            if path == "/api/strategies/_lib" and method == "GET":
                return 200, {"text": "step_templates:\n  gather: …"}
            return 404, {"detail": "not found"}

    client = ApiClient(transport=_FakeTransport())
    ws = ApiWorkspace(client, tmp_path, ChangeLog())
    text = ws.read_text("strategies/_lib.yaml")
    assert "step_templates" in text
    with pytest.raises(WorkspaceError, match="锁定文件"):
        ws.write_text("strategies/_lib.yaml", "step_templates: {}")


def test_strategy_save_hints_ride_the_lint_channel(tmp_path: Path):
    """策略保存成功后的可读性提示走 memory lint 同一条通道（写结果尾部点名）。"""
    from agent.client import ApiClient
    from agent.workspace import ApiWorkspace, ChangeLog

    VALID = {"strategy": {}, "assembly": {}}   # 占位：transport 只对 save 返回 hints

    class _FakeTransport:
        def __call__(self, method, path, body):
            if method == "GET" and path == "/api/strategies":
                return 200, []                  # 不存在 → create → PUT
            if method == "POST" and path == "/api/strategies":
                return 200, {"id": "s"}
            if method == "PUT" and path == "/api/strategies/s/doc":
                return 200, {"ok": True, "errors": [],
                             "hints": [{"text_zh": "step 'w' 缺 display_name_zh"}]}
            return 404, {"detail": "not found"}

    ws = ApiWorkspace(ApiClient(transport=_FakeTransport()), tmp_path, ChangeLog())
    ws.write_text("strategies/s.yaml", yaml_dump_two_segments())
    hints = ws.drain_lint_hints()
    assert any("display_name_zh" in h for h in hints)


def yaml_dump_two_segments() -> str:
    return ("strategy:\n  id: s\n  group_slots: [main]\n  initial_step: w\n"
            "  steps:\n    - step_id: w\n      branches:\n        - do: []\n"
            "  edges: []\n"
            "assembly:\n  id: a\n  groups:\n    - group_id: G\n      composition:\n"
            "          terran/marine: {min: 1, target: 1, max: 1}\n"
            "  strategy_instances:\n    - instance_id: s1\n      strategy_ref: s\n"
            "      bindings: {main: G}\n      params: {}\n")
