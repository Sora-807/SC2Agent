# ADR-0005 agent：第一代 no_think + intent router

- 状态：已确认草案
- 替代：无；router 实现模型在本 ADR 内最终确定
- 范围：`agent` 模块 V1 运行策略

## 背景

旧 agent 的尴尬处境：

- 简单指令也要进入完整 LLM 思考，响应慢。
- 复杂 flow 期望 LLM 临场编写，结果写不出。
- 战斗中写新逻辑导致大量错误与反复修改。

重新讨论后确定：第一代 live 采用 `no_think + intent router`，优先把“已有模块的插拔组合和参数修改”做到可靠，后期再优化深度思考。

## 决定

### 1. V1 live 策略

```text
live_policy = "no_think"
```

- 对局中不进入 LLM 推理回合。
- `IntentRouter` 是组件名；V1 实现为混合 router：

```text
IntentRouter
├─ FastRuleTable       高频固定指令直接命中
│    dispatch / clear_tactics / start / stop / look 等
│
└─ NoThinkLLMParser    自然语言与复合句
     输入：用户消息 + 当前 FlowSpec + 模块目录 + patch schema
     输出：严格 JSON patch
     关闭推理 / 无工具 / 无自由文本 / 单次调用
         ↓
PatchValidator
     schema 校验 + 模块存在性 + 参数范围 + placement 校验
     不合法 → 拒绝，不二次猜测
```

- router 只做“意图理解 + 槽位提取 + 结构化 patch”，不做开放生成。
- 无法识别或校验失败时明确回复“当前不支持”，不硬猜。
- 对局中禁止创建新模块。

### 2. Router 操作面

```text
list_modules
get_module
read_flow
flow_edit(patch)          # insert/remove/move/set_param/set_trigger/set_order
retrigger_instance(instance_id)  # 不改 FlowSpec，不重新 validate/simulate，写 flow_event
validate_flow
simulate_flow
commit_flow
dispatch_tactics
clear_tactics
look
```

### 3. 提交门槛

每次 flow 修改自动经过：

```text
validate → simulate → commit
```

- 通过：提交生效。
- 不通过：返回结构化错误，不提交。
- 任何提交路径都不得绕过校验。

### 4. 离线创作边界

以下内容只在离线模式进行：

- 设计新模块。
- 编辑模块内部策略。
- 测试与验收模块。
- 复杂长期规划与复盘。

### 5. 未来思考深度分层（V1 不实现，仅保留方向）

| 层 | 是否 LLM | 职责 |
|---|---|---|
| 执行器 | 否 | 逐帧运行 FlowIR，应用已确定的操作 |
| 意图路由 | 非思考 LLM + 规则表 | V1 重点，语言 → 结构化 patch，禁止开放生成 |
| 局内浅思考 | LLM | 选模块、调参数、顺序调整，禁止创作 |
| 离线深度思考 | LLM | 设计、测试、新增模块、复盘 |

V1 只实现前两层。NoThinkLLMParser 单次输出结构化 patch，不进行推理链。

## 反例（明确禁止）

- 无法识别意图时让 LLM 自由发挥。
- live 中暴露创建模块工具。
- 提交未通过 validate/simulate 的 flow。
- 为了让某条指令“成功”而在 router 里加只匹配单一措辞的特例。

## 验收标准

1. ADR-0012 第 6/7 节的复合句和改口句全部进入 router 验收集，路由为正确 patch；NoThinkLLMParser 无推理链、无工具调用、无自由文本。
2. FastRuleTable 覆盖的高频固定指令不经 LLM。
3. 所有成功路由的操作产出合法 FlowSpec。
4. 所有修改通过 validate 与 simulate 后才提交。
5. 不支持的指令被明确拒绝，不静默误解。
6. `retrigger_instance` 不改 FlowSpec，不重新 validate/simulate，但写 flow_event。
7. 离线模式可创建、测试、入库新模块。
