"""world 模块：RawGameState → GameState 适配层。

坐标/网格约定 + 规则层（D11 起插 mechanics.LayerComputer 补 power/addon）。
V1 只做 alliance→Owner、health→hp 字段对齐 + 中性资源过滤 + 透传。
"""