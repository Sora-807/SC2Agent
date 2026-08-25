# 工作区地图

- `production-plans/<id>.yaml`  生产规划（队列）—— 读写（旧名 plans/ 仍可读）
- `map-plans/<id>.yaml`         地图规划（双分支槽位）—— 读写
- `initial-states/<id>.yaml`    状态快照（simulate 起点 / 会话导出）—— 读写
- `strategies/<id>.yaml`        策略 —— 读写
- `catalog/`                    三族数据手册 —— 只读（从活 catalog 渲染，零漂移）
- `maps/<源>/<bbox>.md`         格点网格 —— 只读
- `memory/`、`scratch/`         记忆与自留地 —— 读写
- `recordings/`、`system/`      对局录像 / 系统说明 —— 只读
