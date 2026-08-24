/**
 * ChatDock 接线锁（P3 切片 A + 2026-08-22 十五轮真流式）——
 * 输入框不再是硬禁用占位（B10 时代的 placeholder 已退役）、走 /api/agent/chat(_stream)、
 * 禁用时给理由（G7）。源码扫描式（同 session-drive.test.ts 的纪律）。
 */
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const src = readFileSync(new URL("../src/shell/ChatDock.tsx", import.meta.url), "utf-8");
const client = readFileSync(new URL("../src/api/agent-chat.ts", import.meta.url), "utf-8");
// 流式事件的转移逻辑在 chat-live.ts（2026-08-23 时间线化），行为断言见 chat-live.test.ts
const live = readFileSync(new URL("../src/shell/chat-live.ts", import.meta.url), "utf-8");

function code(text: string): string {
  return text.replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "");
}

describe("ChatDock：商量回路已接上", () => {
  it("不再有 B10 时代的禁用占位（placeholder 文案退役）", () => {
    expect(code(src)).not.toContain("agent 接入见 B10");
    expect(code(src)).not.toContain("disabled\n");
  });

  it("输入框禁用是有条件的（后端不在 / 有理由）—— **运行中不禁用**（2026-08-24 插话）", () => {
    expect(code(src)).toMatch(/disabled=\{!api\.ok \|\| chatReason !== null\}/);
    expect(code(src)).toContain("interjectChat(text)");   // busy 时回车 = 插话
  });

  it("发送走流式（sayChatStream），历史走 getChat，失败理由原样显形（G7）", () => {
    const c = code(src);
    expect(c).toContain("sayChatStream(text,");
    expect(c).toContain("getChat()");
    expect(c).toMatch(/发送失败/);
  });

  it("客户端打 POST /api/agent/chat（整段回退路径保留）", () => {
    expect(code(client)).toContain('"/api/agent/chat"');
    expect(code(client)).toContain('"POST"');
  });

  it("设计参考落地（deepseek-harness 文档）：思考行（原子图标+着色）+ IN/OUT 卡 + Markdown + 加宽", () => {
    const c = code(src);
    expect(c).toContain("思考");
    expect(c).toContain("AtomIcon");          // SVG 原子图标（不依赖 emoji 字体）
    expect(c).toContain("text-violet-500");   // 思考 = 着色加粗
    expect(c).toMatch(/>IN</);
    expect(c).toMatch(/>OUT</);
    expect(c).toContain("rounded-2xl");       // 用户气泡 vs agent 全宽的不对称
    expect(c).toContain("<Markdown text={m.text} />");  // agent 回复走 Markdown
    expect(c).toContain("w-160");             // 宽度加倍（用户拍板）
    expect(c).toContain("ToolIcon");          // 工具行 SVG 语义图标
  });

  it("提案审批收件箱已退役（2026-08-22 用户拍板：离线域 agent 直改，收件箱纯冗余）", () => {
    const c = code(src);
    expect(c).not.toContain("listProposals");
    expect(c).not.toContain("待审批");
    expect(c).not.toContain("STATUS_TONE");
  });

  it("没有发送按钮（Enter 直发，省地方）；提示行只在出错时出现", () => {
    const c = code(src);
    expect(c).not.toMatch(/>发送</);
    expect(c).not.toContain("Enter 发送 · ");    // 常驻提示行退役（placeholder 里的说明不算）
    expect(c).toMatch(/\{chatErr &&/);          // 出错仍显形（G7）
  });

  it("输入框 5 行（二十二轮用户拍板：两行太矮）", () => {
    expect(code(src)).toContain("rows={5}");
  });

  it("改动按钮（十五轮拍板样式）：蓝框圆角、透明底、黑字、与回复同宽对齐", () => {
    const c = code(src);
    expect(c).toContain("ChangeChip");
    expect(c).toContain("m.changes");
    expect(c).toMatch(/w-full rounded-lg border-\[1\.5px\] border-accent-blue/);
    expect(c).toMatch(/window\.location\.hash = c\.target/);  // 跳转目标后端算好，不信 LLM 拼链接
  });
});

describe("ChatDock：真流式（2026-08-22 十五轮，接 BaseAgent start_stream）", () => {
  it("客户端有 SSE 流（/api/agent/chat/stream + 逐 data: 行解析 + 失败回退整段）", () => {
    const c = code(client);
    expect(c).toContain('"/api/agent/chat/stream"');
    expect(c).toContain('startsWith("data: ")');
    expect(c).toMatch(/sayChat\(text\).*回退|回退.*sayChat/);   // 旧后端/网络失败 → 整段接口
  });

  it("第一个思考 token 即点亮思考行：reasoning 分片直通 ThinkRow（running 态）", () => {
    const c = code(live);
    expect(c).toMatch(/ev\.kind === "reasoning"/);
    expect(c).toMatch(/kind: "reasoning", text: ev\.text, running: true/);
    expect(code(src)).not.toContain("Typewriter");   // 假流式打字机退役（真分片直出）
    expect(code(src)).not.toContain("typing");       // typing 标记随 Typewriter 退役
  });

  it("工具调用从参数分片起就可见（运行中行），完成事件落真名/参数/结果", () => {
    const c = code(live);
    expect(c).toMatch(/ev\.kind === "tool_call"/);
    expect(c).toContain('tool: "…"');                // 参数分片期：名字未知的运行中行
    expect(c).toContain("result_preview");
  });

  it("斜杠指令：/clean 清上下文（输入 / 弹建议，回车/点选执行，不发给 LLM）（2026-08-24）", () => {
    const c = code(src);
    expect(c).toContain("SLASH_COMMANDS");
    expect(c).toContain('"clean"');
    expect(c).toContain("runSlash");
    expect(c).toContain("cleanChat()");
    // 指令被 send 拦截：不走到 sayChatStream
    expect(c).toMatch(/if \(runSlash\(text\)\) return;/);
  });

  it("发话即显「思考中」占位：首个分片到达前不是死屏（2026-08-24 token 流慢的 UI 半边）", () => {
    expect(code(src)).toContain('live.length === 0');
    expect(code(src)).toContain('<ThinkRow text="…" running');
  });

  it("改动 chip 跳对局页前先切 drive 模式（2026-08-24：规划模式守卫曾把它重定向回规划首页）；跳规划页同样先切 offline（复盘页点规划 chip 跳不过去的另一半）", () => {
    const c = code(src);
    expect(c).toContain('c.target.startsWith("#/plan-")');
    expect(c).toContain('setMode("offline")');
    expect(c).toContain('setMode("drive")');
  });

  it("轮末改动刷新规划清单（agent 走 REST 写，前端清单不会自己更新 —— 2026-08-24）", () => {
    const c = code(src);
    expect(c).toContain("useQueueStore.getState().refresh()");
    expect(c).toContain('areas.has("plan") || areas.has("map_plan")');
    expect(c).toContain("refreshSources");
  });

  it("流式轮按到达顺序交错渲染（2026-08-23 分段错位修复）：时间线 + LiveMessage", () => {
    const c = code(src);
    expect(c).toContain("applyLiveEvent(p ?? [], ev)");   // 事件 → 时间线 reducer
    expect(c).toContain("<LiveMessage entries={live}");   // 交错渲染入口
  });

  it("「顾问思考中」扫光占位退役（十五轮）；外层矩形流光 dsh-sweep 也移除（2026-08-24 用户拍板）", () => {
    const c = code(src);
    expect(c).not.toContain("顾问思考中");
    expect(c).not.toContain("runningSteps");         // 1.2s 轮询假流式退役
    expect(c).not.toContain("dsh-sweep");            // 矩形流光去掉了，运行态只留文字
    expect(c).toContain("运行中");                    // 工具行运行态的文字标识还在
  });

  it("round 终态用服务端真源替换本地过程（messages 带 segments/steps/changes 落历史）", () => {
    const c = code(src);
    expect(c).toMatch(/ev\.type === "round"/);
    expect(c).toMatch(/setMessages\(ev\.messages\)/);
  });

  it("A 批（2026-08-24）：segments 交错时间线 + 插话落 live 末尾", () => {
    const c = code(src);
    // AgentMessage 有 segments 优先渲染（工具间正文不再被吞），无 segments 回落旧两段式
    expect(c).toContain("m.segments");
    expect(c).toMatch(/m\.segments\s*\?\s*m\.segments\.map/);
    expect(c).toContain("<UserBubble");
    // 插话进排队条（2026-08-25 用户拍板）：不再插 live 时间线/对话流 ——
    // 输入框上方一行省略显示；轮末服务端 segments 内嵌（记录不丢）+ 排队列清空
    expect(c).toContain("setPendingSays((q) => [...q, text])");
    expect(c).not.toContain("appendUserEntry(l, text)");
    // 插话独立历史条目只喂 LLM：下一条 agent 消息带 segments 时跳过，不重复显示
    expect(c).toMatch(/m\.interjection && messages\[i \+ 1\]\?\.segments/);
    expect(code(client)).toContain("segments?: ChatSegment[]");
    expect(code(client)).toContain("interjection?: boolean");
  });

  it("跟随提醒是系统条不是用户气泡（§0.52：对局没结束系统注入，不冒充用户消息）", () => {
    const c = code(src);
    expect(c).toContain("NudgeBar");
    expect(c).toMatch(/m\.nudge\s*\?\s*<NudgeBar/);   // nudge 标记的消息先进系统条
    expect(c).toContain("var(--warn-fg)");               // warn token 全宽条（主题纪律：不吃裸色）
    expect(code(client)).toContain("nudge?: boolean");     // 契约面：ChatMessage 可带 nudge
  });
});
