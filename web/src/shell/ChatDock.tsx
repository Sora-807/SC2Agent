/**
 * 右侧对话栏 —— 设计语言取自 docs/reference/前端对话框设计指导参考.md（deepseek-harness）：
 * - **刻意的不对称**：用户 = 右对齐圆角气泡（有背景），agent = 全宽纯文本无容器；
 *   不用头像、不用角色文字标签 —— 角色靠几何形态区分；
 * - 思考链 = 内联折叠灰字行（思考 · 首行摘要 → 展开缩进全文）；
 * - 工具调用 = 全宽单行折叠（工具名 · 参数摘要 → 展开 IN/OUT 卡）；
 * - 改动 chip = agent 消息尾部的全宽蓝框按钮（动作 + 对象名），点击跳到对应页面；
 * - **真流式**（2026-08-22 十五轮，接 BaseAgent start_stream）：SSE delta 从第一个
 *   token 起就点亮思考行（轨道旋转 + 呼吸）、正文逐字增长、工具行参数分片时即出现
 *   运行中 —— 轮询假流式与「思考中」扫光占位已退役；
 * - 时间戳 hover 才出现（降低常驻噪音）。
 */
import { useEffect, useRef, useState } from "react";
import {
  getChat, sayChatStream, type ChatChange, type ChatEvent, type ChatMessage, type ChatStep,
} from "../api/agent-chat";
import { useFrames } from "../store/frames";
import { Markdown } from "./markdown";

/** 对话时间戳 = 挂钟时间 HH:MM（fmtTime 是游戏时间格式化器，别拿来用） */
function clock(t: number): string {
  const d = new Date(t * 1000);
  return [d.getHours(), d.getMinutes()].map((n) => String(n).padStart(2, "0")).join(":");
}

const firstLine = (s: string): string => s.split("\n").find((l) => l.trim()) ?? "";

/** 原子图标（SVG —— 不依赖字体 emoji，Windows 上 ☀⚛ 会豆腐块） */
function AtomIcon() {
  return (
    <svg viewBox="0 0 16 16" width="13" height="13" aria-hidden className="shrink-0">
      <g fill="none" stroke="currentColor" strokeWidth="1.1">
        <ellipse cx="8" cy="8" rx="7" ry="3.1" />
        <ellipse cx="8" cy="8" rx="7" ry="3.1" transform="rotate(60 8 8)" />
        <ellipse cx="8" cy="8" rx="7" ry="3.1" transform="rotate(120 8 8)" />
      </g>
      <circle cx="8" cy="8" r="1.2" fill="currentColor" />
    </svg>
  );
}

/** 工具字形（几何符号，常见字体都有；类别一眼可辨） */
function toolGlyph(name: string): string {
  if (name.startsWith("list")) return "☰";
  if (name.startsWith("read")) return "▤";
  if (name.startsWith("create")) return "＋";
  if (name.startsWith("write")) return "✎";
  if (name.startsWith("simulate")) return "▷";
  if (name === "start_session") return "◎";
  if (name === "observe") return "◉";
  if (name === "propose") return "✑";
  return "◆";
}

/** 思考链：内联折叠行 —— 原子图标 + 着色加粗「思考」+ 首行摘要；展开缩进全文。
 * running：轨道旋转 + 文字呼吸（结束即停）；摘要在运行时跟随最新一行。
 * 真流式下 running 从第一个思考 token 起就为真（delta 直通，不再轮询）。 */
function ThinkRow({ text, running = false }: { text: string; running?: boolean }) {
  const last = running
    ? (text.split("\n").filter((l) => l.trim()).at(-1) ?? "")
    : firstLine(text);
  return (
    <details open={running} className={"group/think" + (running ? " think-running" : "")}>
      <summary className="flex h-6 cursor-pointer select-none items-center gap-1.5 text-note">
        <AtomIcon />
        <span className="font-semibold tracking-widest text-violet-500">{running ? "思考中" : "思考"}</span>
        <span className="text-ghost">·</span>
        <span className="min-w-0 flex-1 truncate text-ghost">{last}</span>
      </summary>
      <pre className="max-h-44 overflow-auto whitespace-pre-wrap break-words py-1 pl-[22px] text-note text-ghost">{text}</pre>
    </details>
  );
}

/** 工具调用：全宽单行折叠 —— 字形图标 + 工具名 + 耗时（参数收进展开态，不抢眼睛）。
 * running：行扫光（dsh-sweep 挂在 summary 上，prefers-reduced-motion 自动关）——
 * 参数分片（delta kind=tool_call）到达即进入此态，工具事件（完成）落名收尾。 */
function ToolRow({ step, running = false }: {
  step: Extract<ChatStep, { kind: "tool" }>;
  running?: boolean;
}) {
  const failed = step.preview.startsWith("error:");
  return (
    <details open={running} className={"pl-3 " + (running ? "dsh-sweep rounded" : "")}>
      <summary className="flex h-6 cursor-pointer select-none items-center gap-1.5 text-note">
        <span className="shrink-0 font-mono text-faint">{toolGlyph(step.tool)}</span>
        <span className="shrink-0 text-faint">{step.tool}</span>
        {!running && step.duration_ms > 0 && (
          <span className="shrink-0 tabular-nums text-ghost">{Math.round(step.duration_ms)}ms</span>
        )}
        {running && <span className="shrink-0 text-ghost">运行中…</span>}
      </summary>
      <div className="my-1 space-y-1 rounded-lg border border-l1 bg-inset p-1">
        <div className="flex gap-1">
          <span className="w-7 shrink-0 pt-1 text-right text-mono text-ghost">IN</span>
          <pre className="max-h-32 min-w-0 flex-1 overflow-auto whitespace-pre-wrap break-all rounded py-1 pr-1 text-mono text-dim">{step.args || "{}"}</pre>
        </div>
        <div className="flex gap-1">
          <span className="w-7 shrink-0 pt-1 text-right text-mono text-ghost">OUT</span>
          <pre className={"max-h-32 min-w-0 flex-1 overflow-auto whitespace-pre-wrap break-all rounded py-1 pr-1 text-mono "
            + (failed ? "text-[color:var(--err-fg)]" : "text-dim")}>{step.preview || "（空）"}</pre>
        </div>
      </div>
    </details>
  );
}

/** 流式期间的本地步骤行（running 标记动效；落盘后的 steps 没有这个字段） */
type LiveStep = ChatStep & { running?: boolean };

/** 一条 agent 消息：过程（按事件顺序的内联折叠行）+ 全宽叙述正文 + 改动按钮行。 */
function AgentMessage({ m }: { m: Omit<ChatMessage, "steps"> & { steps?: LiveStep[] } }) {
  return (
    <div className="group/msg">
      {(m.steps ?? []).map((step, i) =>
        step.kind === "reasoning"
          ? <ThinkRow key={i} text={step.text} running={step.running ?? false} />
          : <ToolRow key={i} step={step} running={step.running ?? false} />)}
      {m.text && (
        <div className="py-0.5 text-body leading-6 text-dim">
          <Markdown text={m.text} />
        </div>
      )}
      {(m.changes ?? []).length > 0 && (
        <div className="flex flex-col gap-1 pb-0.5">
          {(m.changes ?? []).map((c, i) => <ChangeChip key={i} c={c} />)}
        </div>
      )}
      <div className="h-5 text-right text-note tabular-nums text-ghost opacity-0 transition-opacity group-hover/msg:opacity-100">
        {clock(m.at)}
      </div>
    </div>
  );
}

function UserMessage({ m }: { m: ChatMessage }) {
  return (
    <div className="group/msg flex flex-col items-end">
      <div className="max-w-[88%] whitespace-pre-wrap break-words rounded-2xl border border-l1 bg-inset px-3 py-1.5 text-body text-strong">
        {m.text}
      </div>
      <div className="h-5 text-note tabular-nums text-ghost opacity-0 transition-opacity group-hover/msg:opacity-100">
        {clock(m.at)}
      </div>
    </div>
  );
}

/** 改动按钮（用户 2026-08-22 拍板样式）：蓝框圆角、透明底、黑字，**与回复同宽**
 *  对齐不缺一块；点击跳到后端算好的 hash（不信 LLM 拼链接）。 */
function ChangeChip({ c }: { c: ChatChange }) {
  return (
    <button
      onClick={() => { window.location.hash = c.target.replace(/^#/, ""); }}
      title={"跳到" + c.target + "（agent 本轮改的东西）"}
      className="w-full rounded-lg border-[1.5px] border-accent-blue bg-transparent px-2 py-1 text-left text-note font-medium text-strong hover:bg-blue-soft"
    >
      {c.action === "add" ? "＋" : "✎"} {c.label}
    </button>
  );
}

export function ChatDock() {
  const api = useFrames((s) => s.api);

  // ---- 对话（P3 切片 A；十五轮起真流式）----
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [chatReason, setChatReason] = useState<string | null>(null);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [chatErr, setChatErr] = useState<string | null>(null);
  /** 流式中的当前轮（事件累积；round 到达后由服务端真源替换） */
  const [live, setLive] = useState<{ steps: LiveStep[]; text: string } | null>(null);
  const bottomRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!api.ok) return;
    let alive = true;
    getChat().then((s) => {
      if (!alive) return;
      setMessages(s.messages);
      setChatReason(s.enabled ? null : (s.reason ?? "对话服务未启用"));
    }).catch(() => alive && setChatReason("对话服务不可达"));
    return () => { alive = false; };
  }, [api.ok]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ block: "end" });
  }, [messages, live]);

  /** SSE 事件 → 本地过程行。规则：
   *  - reasoning 分片追加到「运行中」的思考行（没有就开一行，第一个 token 即点亮）；
   *  - tool_call 参数分片开一行「运行中」工具（名字在完成事件才有）；
   *  - tool_call 完成事件收掉占位行、落真名/参数/结果；
   *  - turn_end 收全部运行态（下一轮的思考另起一行）。 */
  const applyEvent = (ev: ChatEvent): void => {
    if (ev.type === "delta" && ev.kind === "reasoning") {
      setLive((p) => {
        const steps = [...(p?.steps ?? [])];
        const last = steps.at(-1);
        if (last && last.kind === "reasoning" && last.running) {
          steps[steps.length - 1] = { kind: "reasoning", text: last.text + ev.text, running: true };
        } else {
          steps.push({ kind: "reasoning", text: ev.text, running: true });
        }
        return { steps, text: p?.text ?? "" };
      });
    } else if (ev.type === "delta" && ev.kind === "content") {
      setLive((p) => ({ steps: p?.steps ?? [], text: (p?.text ?? "") + ev.text }));
    } else if (ev.type === "delta" && ev.kind === "tool_call") {
      setLive((p) => {
        const steps = (p?.steps ?? []).map((s): LiveStep =>
          s.kind === "reasoning" && s.running ? { ...s, running: false } : s);
        const last = steps.at(-1);
        if (last && last.kind === "tool" && last.running) {
          steps[steps.length - 1] = { ...last, args: (last.args || "") + ev.text };
        } else {
          steps.push({ kind: "tool", tool: "…", args: ev.text, preview: "", duration_ms: 0, running: true });
        }
        return { steps, text: p?.text ?? "" };
      });
    } else if (ev.type === "tool_call") {
      setLive((p) => {
        const steps = (p?.steps ?? []).map((s): LiveStep => ({ ...s, running: false }));
        const i = steps.map((s) => s.kind === "tool" && s.tool === "…").lastIndexOf(true);
        const done: LiveStep = {
          kind: "tool", tool: ev.tool,
          args: ev.args ? JSON.stringify(ev.args) : "{}",
          preview: ev.result_preview ?? "", duration_ms: 0,
        };
        if (i >= 0) steps[i] = done;
        else steps.push(done);
        return { steps, text: p?.text ?? "" };
      });
    } else if (ev.type === "turn_end") {
      setLive((p) => p
        ? { steps: p.steps.map((s): LiveStep => ({ ...s, running: false })), text: p.text }
        : p);
    }
  };

  const send = (): void => {
    const text = input.trim();
    if (!text || busy) return;
    setBusy(true);
    setChatErr(null);
    setLive({ steps: [], text: "" });
    setMessages((m) => [...m, { role: "user", text, at: Date.now() / 1000 }]);
    setInput("");
    sayChatStream(text, (ev) => {
      if (ev.type === "round") {
        if (ev.error) {
          setChatErr(ev.error);   // G7：失败理由原样显形
          setLive(null);
        } else if (ev.messages) {
          setMessages(ev.messages);
          setLive(null);
        }
      } else {
        applyEvent(ev);
      }
    }).catch((err: Error) => {
      setChatErr(err.message);
      setLive(null);
      setMessages((m) => [...m, { role: "agent", text: `（发送失败：${err.message}）`, at: Date.now() / 1000 }]);
    }).finally(() => setBusy(false));
  };

  const inputPlaceholder = !api.ok
    ? "后端未连接"
    : chatReason
      ? `对话不可用：${chatReason}`
      : busy
        ? "顾问正在想…"
        : "和 agent 商量打法：读规划、试算、比战术（Enter 发送，Shift+Enter 换行）";

  return (
    <aside className="flex w-160 min-h-0 flex-1 flex-col overflow-hidden bg-panel">
      <div className="flex items-center gap-2 border-b border-l1 px-3 py-2">
        <span className="font-semibold text-strong">对话</span>
      </div>

      <div className="min-h-0 flex-1 space-y-3 overflow-auto p-3">
        {messages.length === 0 && !live && (
          <div className="text-note text-ghost">
            和顾问说第一句话（例：「看看默认规划，试算 300 秒，告诉我卡在哪」）。
            它能直接读改生产/地图规划、干跑试算、记忆你的偏好。
          </div>
        )}
        {messages.map((m, i) => m.role === "user"
          ? <UserMessage key={i} m={m} />
          : <AgentMessage key={i} m={m} />)}
        {live && (
          <AgentMessage m={{ role: "agent", text: live.text, at: Date.now() / 1000, steps: live.steps }} />
        )}
        <div ref={bottomRef} />
      </div>

      <div className="border-t border-l1 p-2">
        <textarea
          rows={5}
          disabled={!api.ok || busy || chatReason !== null}
          placeholder={inputPlaceholder}
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              send();
            }
          }}
          className="w-full resize-none rounded border border-l1 bg-inset p-2 text-body text-dim placeholder:text-ghost"
        />
        {/* 出错才占这一行（G7）；平时不留常驻提示文字（用户拍板省地方） */}
        {chatErr && (
          <div className="mt-1 text-right text-note">
            <span className="text-[color:var(--err-fg)]">{chatErr}</span>
          </div>
        )}
      </div>
    </aside>
  );
}
