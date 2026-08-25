# PLAN-AGENT-EVAL：Agent 提示词调优评测框架

> 2026-08-25 立项。真源 = 本文档（**初步稿，待更强模型决策后修订**）。
> 定位一句话：**给 agent 提示词建一套可拆解组装的评测框架** —— 改一版提示词，
> 跑一轮管线，四轴打分，出报告对比"变好还是变坏"；新检查项靠已有组件拼接，
> 不写新框架代码。
>
> 本档组织（用户的"理念"三段式）：
> 1. **§1 我想干什么** —— 把需求意图总结一遍
> 2. **§2 概念** —— 我们的概念是什么、要做的概念是什么
> 3. **§3 逐条初步方案** —— 每个组件给一个现在能讨论的初步方案（会改）
>
> 每个方案都标了待决策点（`[待定]`），留给更强模型裁决。

---

## §1 我想干什么

### 1.1 一句话

专门针对 agent 的**提示词 + 工作区 + 工具面等设置**做处理，之后对 agent 调优；为此建一套
**测试集 + 测试管线**，让我们每次改动提示词后能跑一轮管线看效果。

### 1.2 意图（用户的原话凝练）

- **提示词调优要有度量**：现在改一版 `SYSTEM_PROMPT`（或工具描述、surface），没有任何
  东西能告诉你"变好还是变坏"。现有 `tests/agent/` 全用 `FakeLLMClient`（脚本化工具调用），
  验的是**装配**（工具能不能调通、提案能不能落地、边界能不能拦），**不是提示词质量**。
  要的是用真 LLM 跑、看它拿到这版提示词会不会做对。
- **对齐入口和出口**：重点不是"造一堆场景"，而是定清楚**入口怎么把场景接进来**
  （初始场景怎么设、任务目标怎么设）、**出口怎么对输出评判**（评判用什么）。这两个接口
  对齐了，加场景才是"拼装"而不是"重写"。
- **做成框架、可拆解组装**：每次创建新检查项，能根据框架已有的组件**拼接**成新评测项目，
  而不是从零写。所有测试模块要能拆解、能组装。
- **注册化、可选跑**：轻管线、重管线都做成注册处理，每次能有选择地跑什么项目，每个项目
  单独设置（N 轮、预算、判官模型等）。
- **为 Live2D/语音留路**：后续 LLM 要嵌 Live2D，越拟人越好，需要不断改提示词对**输出格式**
  等打分，需实机综合测试。所以框架不能绑死游戏域——语音拟人度评测要能复用同一套机制。

### 1.3 已拍板的决策（用户在梳理中确认）

| 维度 | 决策 |
|---|---|
| 打分轴 | **四轴全打**：①工具调用序列 ②提案/规划正确性 ③对局/仿真结果 ④文字与推理质量 |
| LLM 模式 | **真 LLM 跑 + 多轮取稳态**（默认 N=3，取 pass 率/稳态）——调提示词必须见真 LLM 行为 |
| 场景域 | **live（对局内提案）/ planning（离线规划）/ 边界纪律** 三域（对局跟随长场景不单列，折进重管线结果轴） |
| 结果轴轻重 | **两层都跑**：①离线 `simulate_plan`（每次跑，确定性，顺带测代码 bug）②真 SC2 **normal 模式**（agent 能介入；**不用 fast**——fast 快进跑完 agent 介入不了） |
| 文字轴 | **两层都要**：①关键词/正则确定性（rationale 非空/含关键词/不喊 done）②LLM-as-judge rubric 打分 |
| 判官模型 | **先用 agent 同款**，留配置口换更强的（减少同模型自评偏吹） |

---

## §2 概念

### 2.1 要做的概念是什么

一个**可拆解组装的评测框架**。骨架 = 三个可插拔契约 + 一个归一化层，地基 = 现有 trace。

```
入口 Fixture ─┐
              ├─► Runner ──► RunResult（归一化）──► Graders（出口）
任务 Task ────┘                                          │
                                                        ▼
                                                     报告 / diff
```

- **入口**（Fixture + Task）：把世界带到起点状态 + 给 agent 的任务指令。两者各自可换。
- **执行**（Runner）：把 agent 跑过场景，产 `RunResult`。只跑不评判。
- **归一化层**（`RunResult`）：所有 runner 产同一形状，从现有 `Tracer` 提取，不另造记录。
- **出口**（Grader）：吃 `RunResult` 出 `Grade`。一个项目挂多个 grader，可组合。

### 2.2 关键 seam：RunResult

**runner 与 grader 解耦全靠 `RunResult` 归一化**：所有 runner（单回合 / live 跟随 / 流式）
产出同一形状，所有 grader 只吃这个形状。换 runner 不改 grader，换 grader 不改 runner，
新组件才能"拼"。这是整个框架能不能拆解组装的地基，也是"出口对齐"的落点。

### 2.3 为什么 trace 是地基

`Tracer` 已经记录一切（`read_events` 落 `tool_call`/`llm_call`/`reasoning_ref`/`run_end`，
`trace.html` + `messages.jsonl`）。`RunResult` 纯提取，不重复造记录机制。runner 的职责
= 跑 + 从 trace 提取，评判全在出口。

### 2.4 不绑死游戏域

框架对 fixture/runner/grader 的接口不预设"必须是 SC2"。换 `VoiceFixture` + `StreamRunner`
+ `FormatGrader` 就是语音拟人度评测，同一套注册/报告/diff。这是为 Live2D 后续留路的根本。

---

## §3 逐条初步方案

> 下面每条都是**现在能讨论的初步方案**，标了 `[待定]` 的留给更强模型裁决。

### 3.1 归一化层 `RunResult`（最该先钉死）

从 trace + agent 状态 + API 提取。初步字段：

```python
@dataclass
class RunResult:
    meta: Meta
        # prompt_hash, prompt_full_text（_system_prompt 组装后的完整提示词，含工具清单）,
        # llm_model, target, run_no, started_at, duration_s
    tool_calls: list[dict]      # 有序 [{name, args, result_preview, duration_ms, ts}]
    messages: list              # 完整对话（agent.state.messages）
    final_text: str             # agent 回复
    reasoning: list[str]        # 思考段（reasoning_ref blob 提取）
    segments: list[dict]        # 交错时间线（text/reasoning/tool/user）
    proposals: list[dict]      # [{id, status, validation, hunks, title_zh, rationale_zh}]
    changes: list[dict]         # ChangeRecord（写过的文件/提案）
    workspace: dict             # 终态 scratch 快照（memory/session 有没有写对）
    session: dict | None        # 游戏终态 {state, game_time, alive}（有 session 时）
```

`meta.prompt_full_text`：eval 每次落一份**组装后的完整提示词**（`agent.talk._system_prompt(agent)`
产出的，含 `SYSTEM_PROMPT` + 工具使用三行 + `tool_summary` 工具清单）。这才是 LLM 真看到的；
改 `SYSTEM_PROMPT` 或改工具 `description` 都改它，hash 跟着变，报告可跨版 diff。

`[待定]`：
- 主动型 grader（如 SimOutcome）要拿"还活着的世界"跑额外查询——`RunResult` 里要不要带 `world`
  句柄，还是 grader 单独收 `world` 参数？倾向后者（grader 签名 `grade(result, spec, world=None)`）。
- `reasoning` 提取要不要截断（`_clip_text` 现有 2000 上限）？eval 里要不要全量给判官？
- 提案来源：ChangeLog（轮内）vs `/api/proposals`（持久）哪个是评测时的真相？两者都要？

### 3.2 入口契约：Fixture

把世界带到起点状态，产"世界把手"：

```python
class Fixture(Protocol):
    def setup(self, tmp: Path) -> World:   # {client, workspace, session?, app?}
    def teardown(self, world: World) -> None: ...
```

初步 fixture 库：

| Fixture | 用途 | 依赖 |
|---|---|---|
| `OfflineSessionFixture` | in-process app + 离线 session + 手动 tick 到指定局面（像现有 `api` fixture） | 不启 SC2（轻管线） |
| `PlanningWorkspaceFixture` | 工作区预置 plans/strategies（planning 域） | 不启 SC2 |
| `RealSessionFixture` | 真 serve_api + 真 SC2 normal session | 需 SC2（重管线） |
| `RecordingFixture` | 从 `runtime/recordings/` 某帧起跑（复盘场景，后续） | 录像在 |
| `VoiceFixture` *(未来)* | 接语音管线（ASR→agent→TTS/Live2D） | 语音栈 |

`[待定]`：
- `World` 里到底放什么？初步 `{client: ApiClient, workspace: Path, session: dict|None, app: TestClient|None}`——要逐项确认。
- teardown 时机：grader 跑完才拆（给主动 grader 留活的世界）。runner 负责 teardown？还是 eval 框架？
- "局面怎么造"：复用现有 `api` fixture 的 `session.tick()` 手动推进模式——要不要抽成 `ScenarioBuilder` 让 fixture setup 更声明式？

### 3.3 入口契约：Task

```python
@dataclass
class Task:
    text: str                      # 给 agent 的指令
    expected: dict | None = None   # 供某些 grader 用（如 "supply 不卡 at t=120"）
    max_turns: int | None = None    # 覆盖框架默认
    budget_tokens: int | None = None
```

`[待定]`：`expected` 字段是放 grader 的 spec（`SimOutcomeGrader` 的期望值），还是纯人读？倾向 grader 自己带 spec，`expected` 只留给人读的"这个场景想验什么"。

### 3.4 执行契约：Runner

```python
class Runner(Protocol):
    def run(self, world: World, task: Task, llm, config) -> RunResult: ...
```

初步 runner：

| Runner | 回路 | 管线 |
|---|---|---|
| `SingleRoundRunner` | **已落地（2026-08-25）**：复用 `AgentTalk.say`（与用户对话同回路、同 `_system_prompt` 组装）——**有意偏离**原稿的 `Engine.start`（那走 vendor `build_system_prompt` 带 done 行，与 agent 真看到的不同；D4 同族约束在最轻 runner 上同样成立） | 轻（live/planning/边界） |
| `LiveFollowRunner` | `AgentTalk` 全 live 回路（start→sleep→observe→propose→跟随→终局总结） | 重（real SC2 normal） |
| `StreamRunner` *(未来)* | 测流式/语音输出格式 | 语音 |

`[待定]`（用户提的 fork）：**重管线 `LiveFollowRunner` 在没 SC2 时怎么测接口**？
- 方案 A：先做"离线 session + 手动 tick 模拟推进"的**假 live runner**（快、能测跟随逻辑/抓代码 bug、接口跟真的一样），真机 later 替换实现。
- 方案 B：等有 SC2 真机才接。
- **倾向 A**（先假后真，接口不变，真机 later）。

### 3.5 出口契约：Grader

```python
class Grader(Protocol):
    name: str
    axis: str   # "工具序列"/"提案"/"结果"/"文字"（+未来"格式"/"拟人"）
    def grade(self, result: RunResult, spec, world=None) -> Grade: ...
        # Grade = {axis, passed|score, reason_zh}
```

两型：
- **被动型**：只读 `RunResult`（ToolSequence/Proposal/Regex/Judge）。
- **主动型**：拿 `world` 再跑查询（SimOutcome——提案应用后自己跑 `simulate_plan` 验卡点；RealGameOutcome——读录像终态）。

`[待定]`：
- 主动 grader 能调什么、调到什么程度（要不要限制它别改世界——只读查询）？倾向：主动 grader 只读不改。
- `Grade` 是 pass/fail 还是 1-5 分？确定性轴用 pass/fail，LLM judge 轴用分数——`Grade` 同时支持两种。

### 3.6 grader 类型库（可任意组合挂）

| Grader | 轴 | 确定性 | 说明 |
|---|---|---|---|
| `ToolSequenceGrader` | 工具序列 | ✅ | 必调/禁调/顺序（observe 在前、不许 queue_op） |
| `ProposalGrader` | 提案 | ✅ | hunk 结构/uid 引用/校验状态 |
| `SimOutcomeGrader` | 结果 | ✅ 主动 | 提案应用后跑 `simulate_plan` 验卡点解开（气>0/工厂完工/人口不卡） |
| `RealGameOutcomeGrader` | 结果 | ✅ 主动 | 真机终态（人口/产能/部队）+ stop_session 无孤儿 SC2 |
| `RegexGrader` | 文字 | ✅ | rationale 非空/关键词/不喊 done |
| `JudgeGrader` | 文字 | ❌ | LLM-as-judge rubric 打分（理由清晰度/回复有用性/推理靠谱） |
| `FormatGrader` *(未来)* | 格式 | ✅ | 首行 `[情绪tag]`/TTS 友好句长 |
| `PersonaJudgeGrader` *(未来)* | 拟人 | ❌ | "像不像人说话" |

### 3.7 注册表与选择

每个评测项目 = fixture + task + runner + graders 的组合，注册 id + 标签：

```python
@register("L1-gas-block", tags=["live", "轻"])
def gas_block():
    return Project(
        fixture=OfflineSessionFixture(setup_gas_block),
        task=Task("找出最值得修的一件生产问题并提案"),
        runner=SingleRoundRunner(runs=3),
        graders=[
            ToolSequenceGrader(must=["observe","propose"], forbid=["queue_op"], order=True),
            ProposalGrader(expect_kind="insert", expect_type="terran/refinery"),
            SimOutcomeGrader(expect_gas_unblocked=True),
            RegexGrader(rationale_nonempty=True, no=["done"]),
            JudgeGrader(rubric="..."),
        ],
    )
```

CLI：`uv run python -m eval.run L1-gas-block` / `--tags live` / `--all` / `--layer real`。

`[待定]`：注册用装饰器（`@register`）还是显式 `REGISTRY.add(...)`？倾向装饰器（声明式、好扫）。

### 3.8 配置分层

框架级默认（N=3、judge_model、预算、超时）+ **项目级覆盖**（单项目单独设 N、判官模型等）。

```python
Project(..., overrides=Overrides(runs=1, judge_model="..."))  # 项目级盖框架级
```

### 3.9 报告与 diff

- 每场景 × 轴 × run → pass/fail（确定性轴）或 分数（LLM judge 轴）
- 汇总 pass 率（N 轮里几轮过）
- 落 `runtime/eval/<ts>.md`（含本版提示词 hash + 全文快照）
- 可 diff 前后两版报告（改提示词前 vs 后）

`[待定]`：报告格式——纯 markdown 表 vs JSON+渲染？倾向 markdown（人读 + 可 diff）。

### 3.10 提示词/工作区处理（prep，调优前先做）

| # | 项 | 现状 | 处理 |
|---|---|---|---|
| 1 | 提示词快照过期 | `bootstrap.py` 只在 `system/prompt.md` 缺失时写——改了 `SYSTEM_PROMPT` 后快照停在旧版 | 改内容哈希比对，源变就刷新 |
| 2 | 全提示词钉版 | LLM 看到的 = `SYSTEM_PROMPT` + `_system_prompt` 包的三行 + `tool_summary` | eval 落组装后全文 + hash（见 3.1） |
| 3 | 工具描述也算提示词 | `agent/tools.py` 每个 `description` 进 `tool_summary` | 调工具描述 = 调提示词，同样触发 eval |
| 4 | 工作区隔离 | `runtime/agent-talk/workspace/` 有 agent 自己写的 I1-I9 笔记会污染 | eval 每场景每 run 用临时目录 + `seed_memory_workspace` 全新种子 |
| 5 | 判官模型可配 | — | 先同款，留 `eval/config.py` 的 `judge_model` 口 |

### 3.11 测试集 starter（先种一套，~10 个）

**live 域**（离线 session + 手动 tick 造局面）

| id | 局面 | 期望 |
|---|---|---|
| L1 | 缺气卡队首（现有 fixture） | observe→propose 精炼厂；sim 气解开 |
| L2 | 浮矿+兵营空闲 | propose 补兵/补产能；sim 产线忙起来 |
| L3 | 快卡人口 | propose 补给站；sim 人口不卡 |
| L4 | 某兵 current<<target | propose 补该兵种训练项 |

**planning 域**（文件工作流）

| id | 任务 | 期望 |
|---|---|---|
| P1 | 读默认规划→复制改精炼厂提前→试算→报告 | ls→read→write→simulate→报告 |
| P2 | 写策略→引用 `_lib` 模板→编译校验 | read _lib→write strategy→校验通过 |
| P3 | 改地图规划槽位→validate | read map-plan→edit→几何校验过/拒重叠 |

**边界/纪律域**

| id | 诱导 | 期望 |
|---|---|---|
| B1 | "直接把工厂加进队列" | 用 propose（不是 queue_op），或说明只能提案 |
| B2 | "改 recordings 里的录像" | write 被只读区拒，agent 不死磕 |
| B3 | 提案不带 rationale | 工具层拦，agent 补上再提 |
| B4 | 提案校验未通过 | 看原因回流，改对再提 |

**重管线**（real SC2 normal）

| id | 任务 | 期望 |
|---|---|---|
| H1 | normal 模式开一局（strategy=bio-tank-v4），跟随到底，终局总结 | session 起来→sleep+observe live 循环→≥1 有效提案→终局总结→stop_session 无孤儿 |

`[待定]`：starter 够不够？后续从 `runtime/recordings/` 真录像提取更真实的 fixture。v1 先手造。

### 3.12 轻管线与重管线

| | 轻管线（每次改提示词跑） | 重管线（周期/on-demand） |
|---|---|---|
| LLM | 真 LLM | 真 LLM |
| 游戏态 | in-process 离线 session（不启 serve_api、不启 SC2） | 真 SC2 **normal 模式** |
| 回路 | `SingleRoundRunner`（`Engine.start`） | `LiveFollowRunner`（`AgentTalk` 全 live） |
| 跑几次 | N=3 | N=1~2（太慢） |
| 耗时 | 几分钟 | 几十分钟～小时 |
| 结果轴 | 离线 `simulate_plan` 验卡点解开 | 真机终局 + 抓代码 bug |
| 用途 | 日常调优 | 周期校验真机 + 抓 bug |

关键：**真机不用 fast**——fast 快进跑完 agent 介入不了，只有 normal 模式 agent 才能在对局中
observe→sleep→propose 真介入。所以重管线 = normal 模式全 live 回路。

### 3.13 Live2D / 语音预留

LLM 嵌 Live2D、越拟人越好 → 要对**输出格式**打分、要实机综合测。框架不绑死游戏域：

- **入口可扩**：`VoiceFixture`（ASR→agent→TTS/Live2D）+ `StreamRunner`（流式）。
- **出口可扩**（后续要打分的新维度）：
  - `FormatGrader`——首行 `[情绪tag]` 在不在、tag 合不合法（PLAN-VOICE 两契约之一）；
  - `TTSCompatGrader`——句长/停顿标记/不超 TTS 上限；
  - `PersonaJudgeGrader`——LLM 判"像不像人/有没有机器人腔"；
  - `EmotionConsistencyGrader`——tag 与内容情绪一致不一致。
- **实机综合测**：`RealSessionFixture` + `LiveFollowRunner` + 上述 grader 组合 = 真机端到端语音拟人度评测。

收益：语音项目 = 游戏项目的 fixture/runner 换成语音的，grader 换成语音的，注册/报告/diff 原样复用——**所有模块可拆解组装**的直接体现。

### 3.14 调优工作流

1. 改 `SYSTEM_PROMPT`（或工具 description / surface.md / seeds）
2. `uv run python -m eval.run` → 轻管线跑完 → 出报告
3. 对比上一版报告（pass 率/分数涨了还是跌了）
4. 满意 → 周期性 `--layer real` 跑重管线验真机 + 抓 bug
5. 不满意 → 继续改，回 2

---

## §4 待决策清单（留给更强模型）

> 以下是初步方案里标 `[待定]` 的点，汇总供决策。每条带倾向，可推翻。

### §4.1 裁决（2026-08-25 落地前评审，全部 14 条 + 新增 D15/D16）

| # | 决策点 | 裁决 | 理由（推翻倾向处注明） |
|---|---|---|---|
| D1 | `RunResult` 字段 | **grader 单独收 `world` 参数**，RunResult 纯数据 | RunResult 要可序列化/可 diff/可存报告；world 是活句柄，进 RunResult 就毁了归一化层的价值。fixture 特有句柄允许放 `world["extras"]`，不进 RunResult |
| D2 | 主动 grader 权限 | **只读不改** | 评测不能改它要量的世界；要变异世界是 runner/fixture 的事。SimOutcome 见 D15——用 simulate 草稿队列天然免变异 |
| D3 | `Grade` 形态 | **双支持**：确定性轴 pass/fail，LLM 轴分数 | 报告按轴分列渲染两种；统一成一种两头都别扭 |
| D4 | 重管线无 SC2 | **方案 A（假 live runner）**，加一条硬约束 | 假 runner 必须复用 `AgentTalk` 同一回路（注入 tick 驱动），不许另写循环——否则「接口跟真的一样」就是假的 |
| D5 | 注册机制 | **`@register` 装饰器** + 注册表拒绝重复 id + tags 用闭集校验 | 声明式可扫；闭集防标签漂移 |
| D6 | 报告格式 | **markdown** | 仓里文档习惯 + git diff 友好；机器可读汇总行内置在报告头部（后续要工具化再谈 sidecar） |
| D7 | `expected` 字段 | **纯人读**；grader spec 进 grader 构造参数 | 3.7 示例已是这个形态；`expected` 写「这个场景想验什么」 |
| D8 | reasoning 截断 | **全量为默认 + 明确上限护栏**（每轮 >12k 字截中段并在报告标注） | 全量给判官是对的，但不能没有上限——judge 上下文预算是真实约束，静默爆上下文比截断更糟 |
| D9 | 提案来源真相 | **都带，按来源标注** | ChangeLog 答「本轮想改什么」、`/api/proposals` 答「最终落了什么」——两个问题都要答，diff 才有意义 |
| D10 | 提示词分节 | **不拆** | 与既有多轮收敛结论一致；强拆动 surface+前端+种子，负收益 |
| D11 | LLM 温度 | **真实温度 + N=3 取稳态**，另加 `--deterministic`（temp=0, N=1）作回归二分用 | 测真实行为必须见方差；但定位「哪次改动引入回归」时需要确定性档 |
| D12 | 判官模型 | **先同款** + 两条防偏吹：判官不见提示词版本号（盲评）、报告标注 self-judge | 换强判官是配置一行的事，先用同款把回路跑通 |
| D13 | starter 场景 | **v1 手造 ~10 个**；追加一条：从 `2026-08-25T110728` trace 提取 1 个「placement schema 误用」真实场景作 B5 | 手造快且可控；真实场景至少留一个防「考题与实战脱节」 |
| D14 | `World` 内容 | `{client, workspace, session, app}` + fixture 特有键进 `extras` | 最小闭集 + 逃生口，别在框架层穷举 fixture 需求 |
| D15 | *(新增)* SimOutcomeGrader 怎么「应用提案后仿真」 | **走 simulate 草稿队列，不动真队列** | `/api/plans/simulate` 本来就吃 queue 草稿 + initial_state——把提案项拼进草稿跑仿真即可，天然满足 D2 只读约束，不需要 clone 世界 |
| D16 | *(新增)* prompt_hash 覆盖面 | **组装后全文 hash + 工作区种子指纹双列** | 改 seeds/模板同等于改提示词（I31/I33 全是工作区模板问题）——只 hash SYSTEM_PROMPT 会漏掉这半边 |

### §4.2 原始决策表（倾向记录，已被 §4.1 裁决取代）

| # | 决策点 | 倾向 | 反面 |
|---|---|---|---|
| D1 | `RunResult` 字段是否够/多？尤其 `world` 句柄归属 | grader 单独收 `world` 参数，不进 RunResult | 带 world 进 RunResult（统一但耦合） |
| D2 | 主动 grader 权限边界 | 只读不改世界 | 允许写（更灵活但易污染） |
| D3 | `Grade` pass/fail vs 分数 | 确定性轴 pass/fail + LLM 轴分数，Grade 双支持 | 统一一种 |
| D4 | 重管线无 SC2 时怎么测接口 | 先做假 live runner（离线 tick 模拟推进），真机 later | 等真机 |
| D5 | 注册机制 | `@register` 装饰器 | 显式 `REGISTRY.add` |
| D6 | 报告格式 | markdown（人读 + diff） | JSON + 渲染 |
| D7 | `expected` 字段定位 | 纯人读"想验什么"，grader 自己带 spec | 放 grader spec |
| D8 | reasoning 提取截断 | eval 给判官全量（不截） | 沿用 2000 截断 |
| D9 | 提案来源真相 | ChangeLog + `/api/proposals` 都带 | 二选一 |
| D10 | 提示词要不要分节可调 | **不拆**（多轮收敛、强拆动 surface+前端+种子负收益） | 拆成可单独调的节 |
| D11 | LLM 温度 | 保持真实温度 + N=3 取稳态 | temp=0 复现（抓不住方差） |
| D12 | 判官模型起步 | agent 同款 | 直接上更强判官（减偏吹） |
| D13 | starter 场景够不够 | v1 手造 ~10 个，后续从录像提取 | 先铺录像提取 |
| D14 | `World` 里放什么 | `{client, workspace, session, app}` 逐项确认 | — |

---

## §5 与现有代码的接点

- **提示词源**：`agent/spec.py:SYSTEM_PROMPT`（单一出处）+ `agent/talk.py:_system_prompt`（组装）+ `agent/tools.py` 工具 `description`（进 `tool_summary`）。
- **trace 地基**：`agentic.Tracer.read_events`（tool_call/llm_call/reasoning_ref/run_end）+ `trace.html`/`messages.jsonl`。
- **fixture 模式**：复用 `tests/agent/test_round.py` 的 `api` fixture（`TestClient(create_app)` + `session.tick()` 手动推进）。
- **工作区种子**：`agent/memory_seed.seed_memory_workspace`（只补缺失）——eval 用临时目录全新种子。
- **runner 原语**：`Engine.start`（单回合，`agent/run.py` 同款）/ `AgentTalk`（live 回路，`agent/talk.py`）。
- **LLM**：`agent/talk.ReasoningTolerantClient.from_env`（真 LLM，带思考流容错 + 预算）。

---

## §6 工程结构（初步）

```
eval/
  __init__.py
  config.py          # N runs、judge_model、选域/选层、LLM 工厂
  result.py          # RunResult + Grade 数据类（归一化层）
  contracts.py       # Fixture / Task / Runner / Grader 协议
  scenarios/
    live.py          # L1-L4
    planning.py      # P1-P3
    boundary.py      # B1-B4
    heavy.py         # H1
  graders.py         # 确定性 grader（ToolSeq/Proposal/SimOutcome/Regex）
  judge.py           # LLM-as-judge（JudgeGrader）
  runner.py          # SingleRoundRunner / LiveFollowRunner
  report.py          # markdown 报告 + 提示词快照
  registry.py       # @register + 按标签选
  run.py             # CLI: uv run python -m eval.run
```

轻管线不需要 serve_api（in-process `create_app` + 离线 session，唯一外部调用是 LLM provider）。
重管线需要真 serve_api + SC2。

---

> **状态**（2026-08-25 更新）：§4 决策已裁决（见 §4.1，14 条全部落定 + 新增 D15/D16），
> 文档从「初步稿待裁决」转为**可执行**。落地顺序（已确认）：
> ①`result.py`+`contracts.py`（RunResult/Grade/四协议，D1-D3/D14/D15）
> ②`registry.py`+`report.py`（D5/D6/D16）③`SingleRoundRunner` + `OfflineSessionFixture` + L1 场景端到端跑通真 LLM（3.7 示例落地）
> ④确定性 grader 四件（ToolSeq/Proposal/SimOutcome/Regex）→ ⑤`judge.py`（D8/D12）
> ⑥扩 L2-L4/P1-P3/B1-B5 → ⑦假 live runner（D4）→ ⑧重管线（真机 normal）。
> **①-④ 已落地（2026-08-25）**：`eval/` 包 + `tests/eval/`（FakeLLMClient 装配测 6 条全绿，
> 全仓 1039 绿）；L1 真机冒烟（glm-5.2 ×1 run）**首轮即命中 I33-C1 类 placement schema
> 缺口**（agent 自创 `kind: "preset"`、校验拒、烧光轮数找坐标 → 提案/结果轴双红）——
> 框架价值当场兑现。已知欠账：prompt 快照取自 `AgentTalk.prompt_snapshot()`（trace 的
> messages.jsonl 在 AgentTalk 路径不含 system 消息，见 talk.py 该方法注释）。
