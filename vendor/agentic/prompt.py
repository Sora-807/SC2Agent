"""系统提示词:文件契约模板 + 工具清单,不再注入长篇工作区规范。

- 有 WorkContract:用 build_contract_prompt 生成规则化任务说明。
- 无 WorkContract(多 agent 子任务等):保留 spec.system_prompt() 作为任务说明,
  框架只追加一句工具使用要点和自动编译的工具清单。
"""
from __future__ import annotations

from .contract import WorkContract, build_contract_prompt, tool_summary


def build_system_prompt(agent) -> str:
    """生成系统提示词。agent 有 contract 时走模板,否则走 spec 自由格式。"""
    tools = getattr(agent, "tools", [])
    contract = getattr(agent, "contract", None)
    if isinstance(contract, WorkContract):
        return build_contract_prompt(contract, [tool.name for tool in tools])
    spec = agent.spec
    task_prompt = spec.system_prompt()
    summary = tool_summary(tools)
    return f"""# 任务
{task_prompt}

# 工具使用
- 所有路径都使用相对于工作区根目录的完整路径。
- 已存在的文件要先 read 才能写或改;工具返回 error: 时按提示重试。
- 完成任务后调用 done 提交结果。

# 可用工具
{summary}"""
