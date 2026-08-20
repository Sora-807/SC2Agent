"""Config:静态配置(API key / model 等),不声明 agent。

只是把运行时静态参数(密钥、模型名)打包;引擎用它建 LLM client。agent 类型声明走
register(),不在这里。
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Config:
    api_key: str
    model: str = "glm-5.2"
    base_url: str | None = None
    enable_thinking: bool | None = None       # None=服务端默认;True/False 显式开关
    reasoning_effort: str | None = "high"      # "high"|"max"(思考深度)
