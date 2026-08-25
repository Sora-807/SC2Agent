"""mechanics 模块：三族生产/建造/科技机制插件（待建，占位）。

造价/建造时间/前置 + LayerComputer（power/addon，D11 启用）。只依赖 game。

占位如约挂账（别无声腐烂）：种族机制归这层的约定见 ADR-0002 §4（如 Zerg
幼虫/变形不进 catalog、由 mechanics 处理）；启用条件与拆解留档
docs/REFACTOR.md §5。在它落地前，相关语义由调用层做最小近似并注释标注
（例：planner 的 Zerg 语义钩子，N1 批）。
"""
