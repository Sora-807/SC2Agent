/**
 * 流式轮的**时间线**模型（2026-08-23 用户反馈的分段错位 bug）：
 * 模型经常「吐一段正文 → 继续思考/调工具 → 再吐一段正文」。旧实现把 steps 和 text
 * 分开存、渲染时 steps 全在上、text 钉在最下 —— 后到的思考行出现在已输出正文的上方。
 * 修法：live 状态改为按**到达顺序**的条目列表，正文与思考/工具行各归其位。
 *
 * 纯 reducer：SSE 事件 → 新时间线；组件只渲染，转移逻辑用测试锁。
 * （落盘后的历史消息仍是 {steps, text} 两段 —— 渲染走 AgentMessage 的旧形态。）
 */
import type { ChatEvent, ChatStep } from "../api/agent-chat";

/** 流式期间的本地步骤行（running 标记动效；落盘后的 steps 没有这个字段） */
export type LiveStep = ChatStep & { running?: boolean };

export type LiveEntry =
  | { kind: "text"; text: string }
  | { kind: "step"; step: LiveStep };

/** 时间线里的全部正文拼起来（暂时只用于测试/诊断；渲染直接按条目走） */
export const liveText = (entries: LiveEntry[]): string =>
  entries.filter((e): e is Extract<LiveEntry, { kind: "text" }> => e.kind === "text")
    .map((e) => e.text).join("");

const freezeSteps = (
  entries: LiveEntry[],
  onlyReasoning: boolean,
): LiveEntry[] => entries.map((e) => e.kind === "step" && e.step.running
  && (!onlyReasoning || e.step.kind === "reasoning")
  ? { kind: "step", step: { ...e.step, running: false } }
  : e);

export function applyLiveEvent(entries: LiveEntry[], ev: ChatEvent): LiveEntry[] {
  if (ev.type === "delta" && ev.kind === "reasoning") {
    const last = entries.at(-1);
    // 只有时间线**末尾**还是这条思考行时才续写；中间插过正文/工具就另起一行（按到达顺序）
    if (last?.kind === "step" && last.step.kind === "reasoning" && last.step.running) {
      return [...entries.slice(0, -1),
        { kind: "step", step: { ...last.step, text: last.step.text + ev.text, running: true } }];
    }
    return [...entries, { kind: "step", step: { kind: "reasoning", text: ev.text, running: true } }];
  }
  if (ev.type === "delta" && ev.kind === "content") {
    const last = entries.at(-1);
    if (last?.kind === "text") {
      return [...entries.slice(0, -1), { kind: "text", text: last.text + ev.text }];
    }
    return [...entries, { kind: "text", text: ev.text }];
  }
  if (ev.type === "delta" && ev.kind === "tool_call") {
    let out = freezeSteps(entries, true);   // 工具参数一到，思考行收态
    const last = out.at(-1);
    if (last?.kind === "step" && last.step.kind === "tool" && last.step.running) {
      out = [...out.slice(0, -1),
        { kind: "step", step: { ...last.step, args: (last.step.args || "") + ev.text, running: true } }];
    } else {
      out = [...out, { kind: "step",
        step: { kind: "tool", tool: "…", args: ev.text, preview: "", duration_ms: 0, running: true } }];
    }
    return out;
  }
  if (ev.type === "tool_call") {
    const out = freezeSteps(entries, false);
    const done: LiveStep = {
      kind: "tool", tool: ev.tool,
      args: ev.args ? JSON.stringify(ev.args) : "{}",
      preview: ev.result_preview ?? "", duration_ms: 0,
    };
    const i = out.map((e) => e.kind === "step" && e.step.kind === "tool" && e.step.tool === "…")
      .lastIndexOf(true);
    if (i >= 0) {
      const copy = [...out];
      copy[i] = { kind: "step", step: done };
      return copy;
    }
    return [...out, { kind: "step", step: done }];
  }
  if (ev.type === "turn_end") {
    return freezeSteps(entries, false);
  }
  return entries;   // turn_start / llm_timeout / run_end 不改时间线；round 由服务端真源替换
}
