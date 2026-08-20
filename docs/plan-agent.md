# agent 计划 —— 顾问 agent（第一个闭环已跑通）

> 状态：**第一个闭环已跑通**（真 LLM 提出并落地了一条经过校验的提案）。本文件记已落地的部分 + 后续。
> 关联：`docs/plan-frontend.md` §6（提案审批流）、`docs/plan-backend-view.md`（B7/B10 的 agent 接缝）、
>       `docs（旧）/adr/0005`（no_think + IntentRouter，**主次已反转**，见 §3）、`0009`（观察新鲜度）。

## 0. 已落地

| 件 | 位置 |
|---|---|
| 框架（vendored） | `vendor/agentic`（来源见 `vendor/agentic/NOTICE.md`） |
| api 客户端 | `agent/client.py`（标准库 urllib + 可注入 transport 接缝） |
| 工具（= 权限） | `agent/tools.py`：`observe` / `write_surface` / `propose` |
| 类型声明 + 提示词 | `agent/spec.py` |
| 一个回合 | `agent/run.py`（`--dry` 用 FakeLLM 不打网络） |
| 测试 | `tests/agent/test_round.py`（11 条，全部不打网络） |

## 1. 三条已经成立的边界

**① agent 只能提案，不能直接改状态（§6 P1）—— 靠"不给那个工具"保证**

工具集就是 `{done, observe, write_surface, propose}`。没有 `queue_op`、没有 `set_worker_quota`、
连读写文件的工具都没给。有一条测试直接断言这件事：

> 提示词能被忽略，缺失的工具不能被调用。

**② 读面与 UI 同源（B10）**

`observe` 拿的是 `GET /api/observation` —— 也就是 `latest_at()` 投影出来的**同一批帧**。
没有第二条"从 GameState 直接摘要"的路径，所以 agent 和 UI 不会各说各话。

**③ 新鲜度闭环（R8 / ADR-0009）**

`observe` 的返回里明确写着"提案里的 based_on_seq 用 N"；后端拒过期的。
`ApiError.stale` 让 agent 能区分"世界变了（重取观察再试）"与"请求不合法（别重试）"——
这个区分很重要：混成一句"失败了"会让 agent 在同一个错误上打转。

## 2. 真 LLM 的实际表现（第一次跑）

局面：队首是重工厂（要 100 气），气为 0，队列里没有气的来源，已阻塞 24 秒，
后面 6 个只要矿的机枪兵全被堵住，手上 643 矿。

模型提了**两段式**提案（比我写的 FakeLLM 罐头提案更完整）：

1. `insert` 队首插精炼厂（带 placement）
2. `reorder [0,2,1]` 把机枪兵提到工厂前面

理由里把数字都算了（工厂 100 气 vs 现有 0、精炼厂 75 vs 现有 643、已卡 24 秒）。
双投影确认：当前什么都建不成，提案后精炼厂建成。

说明**观察包给的信息量是够的** —— 这是 B10 设计成"中文 + 段落化 + 带原因"的直接回报。

## 3. 与 ADR-0005 的主次反转（需要确认）

ADR-0005 把 V1 定为 `live_policy="no_think"` + `IntentRouter`（把自然语言快速变成 patch）。
实际落地的形态是**会思考、产出提案、由人审批**的顾问。两者不互斥，但主次反转了：

| | ADR-0005 | 现在 |
|---|---|---|
| 主线 | router：自然语言 → patch，低延迟 | 顾问：读局面 → 提案 → 人审批 |
| router 的位置 | V1 重点 | 后续优化（`dispatch`/`look`/`start`/`stop` 这类低延迟子集） |
| 提交门槛 | validate → simulate → commit 三步必过 | 队列 op 不走 validate（S11）；flow 提交才要 validate + compile（R6） |

第三行不是选择而是事实：ADR-0005 写在生产队列 op 引入之前。

## 4. 后续（未做）

- **常驻触发**：现在是"跑一个回合"。常驻应由**事件驱动**（警报出现、队首阻塞超阈值、
  策略转移），而不是"每隔几秒问一次 LLM"—— 后者在真机上既贵又没必要。
- **持久工作记忆**（ADR-0009 §6）：`DiskWorkspace` 已挂上（`runtime/agent-workspace`），
  但还没让 agent 用它记"计划/失败原因/用户偏好"。
- **拒绝理由回流**：后端已经存了 `decision.comment_zh`，但还没喂回下一轮的 prompt。
  这是 §6 P3 的另一半，**优先级最高的下一步**——否则 agent 会重复推同一个被拒的提案。
- **多 agent**：`agentic` 有 `dispatch`；等有"生产顾问 / 战术顾问"分工时再用。
- **IntentRouter**：把 `start/stop/look` 这类固定指令做成不过 LLM 的快路径。
- **trace 可视化**：`agentic` 自带自包含 HTML trace（`traces/`），还没接进我们的调试页。

## 5. 不做清单

- 不让 agent 直接下命令（§6 P1）——要改就提案。
- 不在 live 中创建/编辑模块与 Strategy（R5）。
- 不改 `vendor/agentic` 的代码：要改行为就在 `agent/` 里包一层。
- 不把密钥入库：`.env` 已在 `.gitignore`。
