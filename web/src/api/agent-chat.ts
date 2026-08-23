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
  /** agent 消息可选：本轮可见过程（工具调用 + 思考） */
  steps?: ChatStep[];
  /** agent 消息可选：本轮落盘的改动（写钩子/自动应用提案收集，轮末随消息进历史） */
  changes?: ChatChange[];
}

/** 一条成功落盘的改动 —— 聊天里渲染成可点击的跳转 chip */
export interface ChatChange {
  area: "plan" | "map_plan" | "live";
  action: "add" | "edit";
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
  | { type: "run_end"; outcome?: string; result?: string; summary?: string }
  | { type: "round"; reply?: string; outcome?: string; changes?: ChatChange[];
      messages?: ChatMessage[]; error?: string };

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
