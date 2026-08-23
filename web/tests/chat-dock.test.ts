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

  it("输入框禁用是有条件的（后端不在 / 思考中 / 有理由），不是永久 disabled", () => {
    expect(code(src)).toMatch(/disabled=\{!api\.ok \|\| busy \|\| chatReason !== null\}/);
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
    expect(c).toContain("toolGlyph");         // 工具行字形图标
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

  it("流式轮按到达顺序交错渲染（2026-08-23 分段错位修复）：时间线 + LiveMessage", () => {
    const c = code(src);
    expect(c).toContain("applyLiveEvent(p ?? [], ev)");   // 事件 → 时间线 reducer
    expect(c).toContain("<LiveMessage entries={live}");   // 交错渲染入口
  });

  it("「顾问思考中」扫光占位退役（十五轮用户拍板：冗余）；工具行运行态保留扫光", () => {
    const c = code(src);
    expect(c).not.toContain("顾问思考中");
    expect(c).not.toContain("runningSteps");         // 1.2s 轮询假流式退役
    expect(c).toContain("dsh-sweep");                // 工具行运行中的扫光还在
  });

  it("round 终态用服务端真源替换本地过程（messages 带 steps/changes 落历史）", () => {
    const c = code(src);
    expect(c).toMatch(/ev\.type === "round"/);
    expect(c).toMatch(/setMessages\(ev\.messages\)/);
  });
});
