"""WorkContract:文件契约模板。

把任务、工作目录、输出目录、输入文档、工作指南规整成一个固定结构,
系统提示词由模板生成,不再手工维护一大段文件契约说明。
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class WorkContract:
    """文件契约:告诉 agent 在哪个目录工作、把结果写到哪个目录。"""
    task: str = ""
    working_directory: str = ""          # 工作目录(空 = 根目录)
    output_directory: str = ""           # 结果必须写入的目录(空 = 工作目录)
    input_paths: list[str] = field(default_factory=list)  # 输入文档/目录(只读参考)
    guidelines: str = ""                 # 自由填写的工作指南


def build_contract_prompt(contract: WorkContract, tool_names: list[str]) -> str:
    """根据 WorkContract 生成规则化的系统提示词。"""
    working_directory = contract.working_directory or "(根目录)"
    output_directory = contract.output_directory or working_directory
    input_paths = "\n".join(f"- {path}" for path in contract.input_paths) if contract.input_paths else "(无,请先 ls/glob 了解工作区)"
    guidelines = contract.guidelines or "(无)"
    tools = "\n".join(f"- {name}" for name in tool_names)
    return f"""# 任务
{contract.task}

# 目录约定
- 工作目录: {working_directory}
- 输出目录: {output_directory}
- 输入文档:
{input_paths}

# 工作指南
{guidelines}

# 工具使用
- 先 ls/glob 了解工作区文件,再 read/grep 读取内容。
- 所有路径都使用相对于工作区根目录的完整路径(例如 inputs/a.md、out/report.md)。
- 写文件只写到输出目录;已存在的文件要先 read 才能写或改。
- 工具返回 error: 时,按提示修正参数后重试。
- 完成任务后调用 done 提交结果。

# 可用工具
{tools}"""


def tool_summary(tools) -> str:
    """从工具列表生成一行一个的简述(name + 首句描述)。"""
    if not tools:
        return "(无)"
    lines = []
    for tool in tools:
        description = (tool.description or "").strip()
        first = description.split(".")[0].strip() if description else ""
        lines.append(f"- {tool.name}: {first}")
    return "\n".join(lines)
