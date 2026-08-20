# vendor/agentic

来源：用户提供的 `C:\dev\project\BaseAgent`（`agentic` 包），按其建议**复制进本仓库**。

## 为什么选它（不是省事）

它的两个特性正好对上本项目的红线：

- **`DiskWorkspace` 物理隔离**（根目录外不可见 / 不可读写、没有 bash/cd）→
  把 agent 的工作区限定在一个 scratch 目录，`R5`「live 中不能创建/编辑模块与 Strategy」
  就从"靠提示词自觉"变成**机制上做不到**。
- **观察策略**「已存在文件必须先 read 才能 write；自读后文件变化会拒绝并提示重读」→
  与 `R8`「旧观察不得作为当前行动依据」同型。我们的 `based_on_seq` 是同一个思路在帧层的实现。

另有 `WorkContract`（任务/输入/输出目录规则化）、多 agent `dispatch`、
trace + 自包含 HTML 可视化、`FakeLLMClient`（测试不打网络）。

## 本仓库的用法

- 不修改 vendor 代码。要改行为就在 `agent/` 里包一层。
- 我们的 agent 只拿到**提案通道**（`propose`），拿不到直接改状态的命令 ——
  §6 P1「agent 只能推提案，不能直接改状态」是靠"不给那个工具"来保证的，
  不是靠提示词请求它别那么做。
- 升级 vendor：重新复制整个目录并重跑 `tests/agent`。
