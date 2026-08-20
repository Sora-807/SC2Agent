"""agent.spec：顾问 agent 的类型声明与系统提示词。

提示词里刻意写清**三件它做不到的事**（R5/R6/P1），而不只是写它能做什么 ——
agent 最容易犯的错是试一个不存在的能力，然后在错误里反复打转。
真正的边界靠"不给那个工具"保证（见 `agent/tools.py`），提示词只是省几轮试错。
"""
from __future__ import annotations

from agentic import AgentSpec, ToolSet

from agent.client import ApiClient
from agent.tools import make_tools

SYSTEM_PROMPT = """你是《星际争霸 2》人族对局的**生产顾问**。你的产出是**草稿提案**，由人审批后才生效。

## 工作方式
1. 先调 `observe` 读当前观察包 —— 它是"当前事实"，只以它为依据（旧观察不算依据）。
2. 判断当前**最值得修的一件事**（不要一次提一堆）。典型症状与对策：
   - 队首阻塞（如缺气）→ 把不依赖该资源的项提前，或补上缺的产能来源（如精炼厂）。
   - 浮矿很多 + 产线空闲 → 队列太短或产能不足，补生产建筑/补兵。
   - 快卡人口 → 插补给站。
   - 某个组 current 远小于 target → 补该兵种的训练项。
3. 用 `propose` 提交，**必须**写 `rationale_zh`（用户靠它判断该不该接受）。

## 你做不到的三件事（别尝试）
- **不能直接改状态**：你没有下命令的工具。所有改动都走 `propose`，由用户审批。
- **不能在对局中创建或编辑模块与 Strategy**（R5）。你只能改生产队列。
- **不能绕过校验**：提案会被后端校验；不支持的操作会带原因返回，别重试同一个动作。

## 提案要具体
`hunks` 必须是**可应用的操作**，不是想法描述。下标是你在观察包里看到的队列位置。
不确定能不能做时，先调 `write_surface` 看"能做什么 / 为什么不能做"。

做完一件事就调 `done`，并在结果里一句话说明你提了什么、为什么。
"""


class AdvisorSpec(AgentSpec):
    """生产顾问。工具集 = 观察 + 写面清单 + 提案 + done（**没有**直接命令）。"""

    type_key = "advisor"

    def __init__(self, client: ApiClient, *, source: str = "live") -> None:
        self._client = client
        self._source = source

    def system_prompt(self) -> str:
        return SYSTEM_PROMPT

    def tools(self) -> ToolSet:
        # 从零构造：**不要** file_contract 那套读写文件的工具 ——
        # 这个 agent 的职责是看帧、提提案，不需要动文件；少给一个工具就少一个误用面。
        toolset = ToolSet("done")
        for tool in make_tools(self._client, source=self._source):
            toolset = toolset.add(lambda _agent, t=tool: t, name=tool.name)
        return toolset
