/**
 * 对话式顾问客户端（P3 切片 A）—— 离线商量的回路。
 *
 * 后端 AgentTalk 常驻同一个 agent 实例：每条消息续同一轮对话（历史在服务端），
 * 这里只发文本、收回复与全量消息列表。LLM 未配置时后端 503 带理由（G7），
 * 前端如实显示而不是把输入框悄悄禁掉。
 */
import { API_BASE } from "../store/frames";

export interface ChatMessage {
  role: "user" | "agent";
  text: string;
  /** epoch 秒（对话时间，不是游戏时间） */
  at: number;
  /** user 消息可选：跟随提醒（对局未结束系统注入的轮输入）——渲染成系统条，
   *  不冒充用户气泡（§0.52 用户拍板） */
  nudge?: boolean;
  /** user 消息可选：轮内插话 —— 已按真实时序进下一条 agent 消息的 segments，
   *  独立条目只喂 LLM（_seed_history），前端渲染时跳过（A 批） */
  interjection?: boolean;
  /** agent 消息可选：本轮可见过程（工具调用 + 思考）—— 旧两段式，segments 的兜底 */
  steps?: ChatStep[];
  /** agent 消息可选：本轮交错时间线（A 批）—— 正文/思考/工具/插话按真实顺序，
   *  工具间正文不再被吞；有它优先于 steps+text 渲染 */
  segments?: ChatSegment[];
  /** agent 消息可选：本轮落盘的改动（写钩子/自动应用提案收集，轮末随消息进历史） */
  changes?: ChatChange[];
}

/** 轮内时间线的最小段（A 批）：与后端 talk._round_segments 同形 */
export type ChatSegment =
  | { kind: "text"; text: string }
  | { kind: "reasoning"; text: string }
  | { kind: "tool"; tool: string; args: string; preview: string; duration_ms: number }
  | { kind: "user"; text: string };

/** 一条成功落盘的改动 —— 聊天里渲染成可点击的跳转 chip */
export interface ChatChange {
  area: "plan" | "map_plan" | "live" | "strategy";
  /** I39 起 open 工具也会出芯片（让用户去看某个文件，不是 agent 改了它） */
  action: "add" | "edit" | "open";
  ref: string;
  label: string;
  /** hash 路由目标（含查询参数），点击 = window.location.hash 赋值 */
  target: string;
}

export type ChatStep =
  | { kind: "tool"; tool: string; args: string; preview: string; duration_ms: number }
  | { kind: "reasoning"; text: string };

export interface ChatState {
  enabled: boolean;
  reason?: string;
  error?: string | null;
  messages: ChatMessage[];
  turns?: number;
  trace_html?: string | null;
  /** 本轮进行中的步骤（流式：思考/工具各自的当前状态，轮询驱动动效） */
  running_steps?: ChatStep[];
}

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(new URL(path, API_BASE).toString(), init);
  if (!res.ok) {
    const body = (await res.json().catch(() => ({}))) as { detail?: unknown };
    throw new Error(typeof body.detail === "string" ? body.detail : `HTTP ${res.status}`);
  }
  return (await res.json()) as T;
}

export const getChat = (): Promise<ChatState> => call("/api/agent/chat");

/** 轮内插话：顾问运行中随时插话（sleep 早醒 / 工具结果捎带）。
 *  queued=false = 当前没有进行中的轮（正常发送即可）。 */
export async function interjectChat(text: string): Promise<{ queued: boolean; reason?: string }> {
  try {
    const res = await fetch(new URL("/api/agent/chat/interject", API_BASE), {
      method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify({ text }),
    });
    if (!res.ok) return { queued: false, reason: `后端返回 ${res.status}` };
    return (await res.json()) as { queued: boolean; reason?: string };
  } catch (err) {
    return { queued: false, reason: "连不上后端：" + (err as Error).message };
  }
}

/** 斜杠指令（2026-08-24 首批只有 clean）：清空对话上下文，记忆文件保留。 */
export async function cleanChat(): Promise<boolean> {
  try {
    const res = await fetch(new URL("/api/agent/chat/clean", API_BASE), { method: "POST" });
    return res.ok;
  } catch {
    return false;
  }
}

export const sayChat = (text: string): Promise<{ reply: string; messages: ChatMessage[] }> =>
  call("/api/agent/chat", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ text }),
  });

/** SSE 流式事件（2026-08-22 十五轮：后端 BaseAgent start_stream 直通）。
 *  delta 从第一个 token 起就有 —— 前端用它即时点亮思考行/流正文/工具运行中。 */
export type ChatEvent =
  | { type: "delta"; kind: "reasoning" | "content" | "tool_call"; text: string;
      index?: number; turn_no?: number }
  | { type: "turn_start" | "turn_end" | "llm_timeout"; turn_no?: number }
  | { type: "tool_call"; tool: string; args?: Record<string, unknown>;
      result_preview?: string; turn_no?: number }
  | { type: "interject_delivered"; texts: string[] }
  | { type: "run_end"; outcome?: string; result?: string; summary?: string }
  | { type: "round"; reply?: string; outcome?: string; changes?: ChatChange[];
      messages?: ChatMessage[]; error?: string };

/** 排队条状态更新（2026-08-25 排队中不消失修复）：送达一条撤一条。
 *  按文本精确匹配；同文本重复排队时先撤最早入队的那条。 */
export function withoutDelivered(
  queue: readonly string[],
  delivered: readonly string[],
): string[] {
  const rest = [...queue];
  for (const t of delivered) {
    const i = rest.indexOf(t);
    if (i >= 0) rest.splice(i, 1);
  }
  return rest;
}

/** 流式说一句：逐事件回调；流面失败（旧后端/网络）自动回退到整段接口。 */
export async function sayChatStream(text: string, onEvent: (ev: ChatEvent) => void): Promise<void> {
  let res: Response;
  try {
    res = await fetch(new URL("/api/agent/chat/stream", API_BASE).toString(), {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ text }),
    });
  } catch {
    res = null as unknown as Response;   // 网络失败也走回退路径
  }
  if (!res || !res.ok || !res.body) {
    const r = await sayChat(text);       // 回退：一次性拿全量（错误照常抛出）
    onEvent({ type: "round", ...r });
    return;
  }
  const reader = res.body.getReader();
  const dec = new TextDecoder();
  let buf = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    let sep: number;
    while ((sep = buf.indexOf("\n\n")) >= 0) {
      const chunk = buf.slice(0, sep);
      buf = buf.slice(sep + 2);
      for (const line of chunk.split("\n")) {
        if (line.startsWith("data: ")) {
          onEvent(JSON.parse(line.slice(6)) as ChatEvent);
        }
      }
    }
  }
}
