/**
 * 流式时间线 reducer（chat-live.ts）—— 用户反馈的分段错位 bug 的行为锁：
 * 「吐正文 → 继续思考/调工具 → 再吐正文」必须按到达顺序交错，
 * 后到的思考行不得出现在已输出正文的上方。
 */
import { describe, expect, it } from "vitest";
import { applyLiveEvent, appendUserEntry, liveText, type LiveEntry } from "../src/shell/chat-live";

const d = (kind: "reasoning" | "content" | "tool_call", text: string) =>
  ({ type: "delta" as const, kind, text });

describe("applyLiveEvent 时间线", () => {
  it("正文分段各归其位：思考 → 正文A → 思考 → 正文B 保持到达顺序", () => {
    let tl: LiveEntry[] = [];
    tl = applyLiveEvent(tl, d("reasoning", "先想"));
    tl = applyLiveEvent(tl, d("content", "正文A"));
    tl = applyLiveEvent(tl, d("reasoning", "又想"));
    tl = applyLiveEvent(tl, d("content", "正文B"));
    expect(tl.map((e) => e.kind)).toEqual(["step", "text", "step", "text"]);
    expect(liveText(tl)).toBe("正文A正文B");
    // 思考行分属两行（中间隔着正文，第二段思考不并进第一段）
    const steps = tl.filter((e) => e.kind === "step");
    expect(steps[0]).toMatchObject({ kind: "step", step: { text: "先想" } });
    expect(steps[1]).toMatchObject({ kind: "step", step: { text: "又想" } });
  });

  it("末尾还是同一条思考行时续写，不另起", () => {
    let tl: LiveEntry[] = [];
    tl = applyLiveEvent(tl, d("reasoning", "a"));
    tl = applyLiveEvent(tl, d("reasoning", "b"));
    expect(tl).toHaveLength(1);
    expect(tl[0]).toMatchObject({ kind: "step", step: { text: "ab", running: true } });
  });

  it("正文连续分片并进同一段", () => {
    let tl: LiveEntry[] = [];
    tl = applyLiveEvent(tl, d("content", "你"));
    tl = applyLiveEvent(tl, d("content", "好"));
    expect(tl).toHaveLength(1);
    expect(liveText(tl)).toBe("你好");
  });

  it("工具：参数分片开运行中占位行，完成事件落真名并收掉全部运行态", () => {
    let tl: LiveEntry[] = [];
    tl = applyLiveEvent(tl, d("reasoning", "想一下"));
    tl = applyLiveEvent(tl, d("tool_call", '{"ref":'));
    tl = applyLiveEvent(tl, d("tool_call", ' "basic"}'));
    // 思考行被工具参数到达收态
    expect(tl.filter((e) => e.kind === "step" && e.step.running).length).toBe(1);
    tl = applyLiveEvent(tl, { type: "tool_call", tool: "read_module", args: { ref: "basic" }, result_preview: "22 项" });
    const tools = tl.filter((e) => e.kind === "step").map((e) => (e as { step: { kind: string; tool?: string } }).step);
    expect(tools.some((s) => s.tool === "read_module")).toBe(true);
    expect(tools.every((s) => !("running" in s) || !(s as { running?: boolean }).running)).toBe(true);
  });

  it("turn_end 收全部运行态；turn_start/run_end 不动时间线", () => {
    let tl: LiveEntry[] = [];
    tl = applyLiveEvent(tl, { type: "turn_start" });
    tl = applyLiveEvent(tl, d("reasoning", "…"));
    tl = applyLiveEvent(tl, { type: "run_end", outcome: "done" });
    expect(tl).toHaveLength(1);
    expect((tl[0] as { step: { running?: boolean } }).step.running).toBe(true);
    tl = applyLiveEvent(tl, { type: "turn_end" });
    expect((tl[0] as { step: { running?: boolean } }).step.running).toBe(false);
  });
});

describe("appendUserEntry 轮内插话（A 批：用户消息落时间线末尾，不进上面历史区）", () => {
  it("插话 append 在已流出内容之后（旧实现 append 进 messages 会跑到最上面）", () => {
    let tl: LiveEntry[] = [];
    tl = applyLiveEvent(tl, d("content", "我看了下队列"));
    tl = applyLiveEvent(tl, { type: "tool_call", tool: "observe", args: {}, result_preview: "ok" });
    tl = appendUserEntry(tl, "等等，先别动气矿");
    expect(tl.map((e) => e.kind)).toEqual(["text", "step", "user"]);
    expect(tl[2]).toEqual({ kind: "user", text: "等等，先别动气矿" });
  });

  it("不修改原数组（纯函数）；随后正文继续落在 user 之后", () => {
    const tl: LiveEntry[] = [{ kind: "text", text: "a" }];
    const out = appendUserEntry(tl, "插");
    expect(out).not.toBe(tl);
    expect(tl).toHaveLength(1);
    expect(applyLiveEvent(out, d("content", "收到")).map((e) => e.kind)).toEqual(["text", "user", "text"]);
  });
});
