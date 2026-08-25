"""eval：Agent 提示词调优评测框架（PLAN-AGENT-EVAL，2026-08-25 立项+裁决）。

骨架 = 三个可插拔契约（Fixture 入口 / Runner 执行 / Grader 出口）+ 一个归一化层
（RunResult，D1：纯数据可 diff，活句柄走 world 参数）+ @register 场景注册（D5）。
CLI：`uv run python -m eval.run [id…] [--tags live] [--all]`。

本包自身的测试不用真 LLM（FakeLLMClient 装配测）；真 LLM 只在 CLI 跑评测时用。
"""
from __future__ import annotations

import sys
from pathlib import Path

# 与 tools/serve_api.py 同款引导：modules/ 只在 pytest 的 pythonpath 里，
# `python -m eval.run` 直接起要自己把源根塞进去（vendor 的 agentic + 根下 agent/）。
_ROOT = Path(__file__).resolve().parent.parent
for _p in (_ROOT / "modules", _ROOT / "vendor", _ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))
