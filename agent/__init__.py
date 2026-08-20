"""agent：SC2 顾问 agent —— 读观察包、推草稿、由用户审批。

位置：最外层（在 api 之上）。它**通过 HTTP 用与 UI 完全相同的入口**（决策 U7）：
没有任何"agent 专用后门"，所以 UI 里能看见 agent 干了什么，agent 能做的 UI 也能做。

授权边界（§6 P1）：agent 只拿到**提案通道**（`propose`），拿不到直接改状态的命令。
这是靠"**不给它那个工具**"保证的，不是靠提示词请求它别那么做 ——
提示词能被忽略，缺失的工具不能被调用。
"""
from __future__ import annotations

import sys
from pathlib import Path

# 源根引导放在**包入口**：`python -m agent.run` 会先执行本文件，
# 那时 run.py 里的 sys.path 设置还没轮到 —— 放在那儿就太晚了。
# （本仓库 `[tool.uv] package = false`，没有安装期的路径配置；pytest 那侧走 pythonpath。）
_ROOT = Path(__file__).resolve().parent.parent
for _extra in (_ROOT / "vendor", _ROOT / "modules", _ROOT):
    if str(_extra) not in sys.path:
        sys.path.insert(0, str(_extra))

from agent.client import ApiClient, ApiError  # noqa: E402
from agent.spec import AdvisorSpec  # noqa: E402

__all__ = ["ApiClient", "ApiError", "AdvisorSpec"]
