"""api：把 ViewFrame 送出去的传输层（B2）。

位置：`view` 之上，最外层。可依赖 view/引擎模块，但**不认识 SC2、不拥有游戏循环**
（那是 driver + B3 的 GameSessionController 的事）。没有任何下层模块 import api。

两条通道，形状直接对着前端的 `FrameSource`（`plan-frontend.md` §4）：
- REST：静态面 + 帧源清单 + 整份 JSONL（复盘/夹具直接 fetch）
- WS：按游戏时间节拍推帧 + 客户端控制（seek/play/pause）—— live 接上后同一条通道不变
"""
from api.app import create_app

__all__ = ["create_app"]
