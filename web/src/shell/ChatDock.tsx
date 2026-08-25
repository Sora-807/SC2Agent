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
import { useEffect, useRef, useState, type ReactNode, type UIEvent } from "react";
import {
  cleanChat, getChat, interjectChat, sayChatStream, withoutDelivered,
  type ChatChange, type ChatEvent, type ChatMessage, type ChatSegment, type ChatStep,
} from "../api/agent-chat";
import { useFrames } from "../store/frames";
import { useQueueStore } from "../planning/queue-store";
import { Markdown } from "./markdown";
import { chatScroll, isNearBottom } from "./chat-scroll";
import { applyLiveEvent, type LiveEntry } from "./chat-live";

/** 斜杠指令（2026-08-24 首批只有 clean）：输入 / 弹建议，回车/点选执行 —— 不发给 LLM */
const SLASH_COMMANDS: { cmd: string; desc: string }[] = [
  { cmd: "clean", desc: "清除对话上下文（记忆文件保留）" },
];

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

/** 工具图标（§0.52 F 批用户拍板语义化，12px SVG / currentColor）：
 *  read=文稿（▤ 像数据库，退役）、glob/grep=放大镜（grep 带横线区分）、
 *  write 族=铅笔、observe=眼睛、sleep=月牙、start/stop_session=进出环/方块。 */
function ToolIcon({ name }: { name: string }) {
  const svg = (children: ReactNode, extra?: Record<string, string>) => (
    <svg width="12" height="12" viewBox="0 0 12 12" fill="none" aria-hidden {...extra}>
      {children}
    </svg>
  );
  const s = { stroke: "currentColor", strokeWidth: 1.1, strokeLinecap: "round" as const, strokeLinejoin: "round" as const };
  if (name.startsWith("read"))                        // 文稿：矩形 + 三行字
    return svg(<>
      <rect x="2.5" y="1.5" width="7" height="9" {...s} />
      <path d="M4.5 4h3.5M4.5 6h3.5M4.5 8h2.5" {...s} strokeWidth={0.9} />
    </>);
  if (name.startsWith("glob") || name.startsWith("grep"))   // 放大镜（grep 镜片里多一横）
    return svg(<>
      <circle cx="5" cy="5" r="3.2" {...s} />
      <path d="M7.6 7.6 10.4 10.4" {...s} strokeWidth={1.4} />
      {name.startsWith("grep") && <path d="M3.4 5h3.2" {...s} strokeWidth={0.9} />}
    </>);
  if (name.startsWith("ls") || name.startsWith("list") || name.startsWith("stat"))
    return svg(<path d="M2 3h8M2 6h8M2 9h5" {...s} />);   // 清单三行
  if (name.startsWith("write") || name.startsWith("append")
    || name.startsWith("edit") || name.startsWith("insert"))
    return svg(<path d="M2.5 9.5 3 7 8.5 1.5 10.5 3.5 5 9l-2.5.5Z" {...s} />);  // 铅笔
  if (name.startsWith("delete"))
    return svg(<>
      <path d="M2.5 4h7" {...s} />
      <path d="M3.5 4v6h5V4M5 6v3M7 6v3M4.5 4l.5-2h2l.5 2" {...s} strokeWidth={0.9} />
    </>);                                                  // 垃圾桶
  if (name === "observe")
    return svg(<>
      <path d="M1.5 6s2-3.5 4.5-3.5S10.5 6 10.5 6 8.5 9.5 6 9.5 1.5 6 1.5 6Z" {...s} />
      <circle cx="6" cy="6" r="1.4" {...s} />
    </>);                                                  // 眼睛
  if (name === "propose")
    return svg(<>
      <rect x="2.5" y="1.5" width="7" height="9" {...s} />
      <path d="M6 4.2v3.6M4.2 6h3.6" {...s} strokeWidth={0.9} />
    </>);                                                  // 文稿 + 加号（提案）
  if (name.startsWith("simulate"))
    return svg(<path d="M3 2 10 6 3 10Z" {...s} />);       // 播放（试算）
  if (name === "sleep")
    return svg(<path d="M9.5 7.5A4 4 0 1 1 4.5 2.5a3.2 3.2 0 1 0 5 5Z" {...s} />);  // 月牙
  if (name === "start_session")
    return svg(<>
      <circle cx="6" cy="6" r="4.4" {...s} />
      <circle cx="6" cy="6" r="1.4" fill="currentColor" />
    </>);                                                  // 进入靶心
  if (name === "stop_session")
    return svg(<rect x="2.5" y="2.5" width="7" height="7" rx="1" fill="currentColor" />);
  return svg(<path d="M6 1.8 10.2 6 6 10.2 1.8 6Z" {...s} />);   // 兜底菱形
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
 * running：只留「运行中…」文字（外层矩形流光 2026-08-24 用户拍板去掉）——
 * 参数分片（delta kind=tool_call）到达即进入此态，工具事件（完成）落名收尾。 */
function ToolRow({ step, running = false }: {
  step: Extract<ChatStep, { kind: "tool" }>;
  running?: boolean;
}) {
  const failed = step.preview.startsWith("error:");
  return (
    <details open={running} className="pl-3">
      <summary className="flex h-6 cursor-pointer select-none items-center gap-1.5 text-note">
        <span className="shrink-0 text-faint"><ToolIcon name={step.tool} /></span>
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

/** 时间线里的用户气泡（live 期间与 segments 里的 user 段共用同一形态：
 *  右对齐圆角气泡，几何上仍与 agent 全宽文本区分） */
function UserBubble({ text }: { text: string }) {
  return (
    <div className="flex flex-col items-end">
      <div className="max-w-[88%] whitespace-pre-wrap break-words rounded-2xl border border-l1 bg-inset px-3 py-1.5 text-body text-strong">
        {text}
      </div>
    </div>
  );
}

/** 一条 agent 消息。segments（A 批）= 轮内交错时间线：正文/思考/工具/插话按真实
 *  顺序渲染，工具间正文不再被吞；旧历史（无 segments）回落 steps+text 两段式。 */
function AgentMessage({ m }: {
  m: Omit<ChatMessage, "steps"> & { steps?: LiveStep[]; segments?: ChatSegment[] };
}) {
  return (
    <div className="group/msg">
      {m.segments
        ? m.segments.map((s, i) =>
            s.kind === "text"
              ? (
                <div key={i} className="py-0.5 text-body leading-6 text-dim">
                  <Markdown text={s.text} />
                </div>
              )
              : s.kind === "reasoning"
                ? <ThinkRow key={i} text={s.text} />
                : s.kind === "user"
                  ? <UserBubble key={i} text={s.text} />
                  : <ToolRow key={i} step={s} />)
        : <>
          {(m.steps ?? []).map((step, i) =>
            step.kind === "reasoning"
              ? <ThinkRow key={i} text={step.text} running={step.running ?? false} />
              : <ToolRow key={i} step={step} running={step.running ?? false} />)}
          {m.text && (
            <div className="py-0.5 text-body leading-6 text-dim">
              <Markdown text={m.text} />
            </div>
          )}
        </>}
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

/** 流式中的当前轮：**按事件到达顺序**交错渲染（正文分段各归其位，思考/工具行不再
 *  跳到已输出正文的上方 —— 2026-08-23 用户反馈的分段错位 bug）。轮内插话（A 批）
 *  也走这里：appendUserEntry 落在时间线末尾，不再插到上面历史区。 */
function LiveMessage({ entries }: { entries: LiveEntry[] }) {
  return (
    <div className="group/msg">
      {entries.map((e, i) => e.kind === "text"
        ? (
          <div key={i} className="py-0.5 text-body leading-6 text-dim">
            <Markdown text={e.text} />
          </div>
        )
        : e.kind === "user"
          ? <UserBubble key={i} text={e.text} />
          : e.step.kind === "reasoning"
            ? <ThinkRow key={i} text={e.step.text} running={e.step.running ?? false} />
            : <ToolRow key={i} step={e.step} running={e.step.running ?? false} />)}
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

/** 跟随提醒条（§0.52 用户拍板）：对局没结束系统不让 agent 停 —— 这是系统注入的
 *  轮输入，不是用户在说话，所以渲染成全宽琥珀条而不是用户气泡；正文即系统对
 *  顾问说的原话（对局进行中 · 必须 sleep 跟随），透明可审计。 */
function NudgeBar({ m }: { m: ChatMessage }) {
  return (
    <div className="flex items-start gap-2 rounded-lg border border-l2 px-3 py-1.5 text-note text-[color:var(--warn-fg)] bg-[color:var(--warn-bg)]">
      <svg className="mt-0.5 shrink-0" width="12" height="12" viewBox="0 0 12 12" fill="none" aria-hidden>
        <circle cx="6" cy="6" r="4.5" stroke="currentColor" strokeWidth="1.3" />
        <path d="M6 3.5V6l1.8 1.2" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" />
      </svg>
      <span className="min-w-0 flex-1 whitespace-pre-wrap break-words">{m.text}</span>
    </div>
  );
}

/** 改动按钮（用户 2026-08-22 拍板样式）：蓝框圆角、透明底、黑字，**与回复同宽**
 *  对齐不缺一块；点击跳到后端算好的 hash（不信 LLM 拼链接）。 */
function ChangeChip({ c }: { c: ChatChange }) {
  return (
    <button
      onClick={() => {
        // 目标页可能不属于当前模式 —— 按目标前缀切模式，否则 App 的
        // 「页面不属于本模式」守卫会把它弹回模式首页（2026-08-24 用户报
        // 复盘页点规划 chip 跳不过去；此前只处理了对局方向这半边）
        if (c.target.startsWith("#/plan-")) {
          if (useFrames.getState().mode !== "offline") {
            void useFrames.getState().setMode("offline");
          }
        } else {
          void useFrames.getState().setMode("drive");
        }
        window.location.hash = c.target.replace(/^#/, "");
      }}
      title={"跳到" + c.target + "（agent 本轮" + (c.action === "open" ? "让你看" : "改") + "的东西）"}
      className="w-full rounded-lg border-[1.5px] border-accent-blue bg-transparent px-2 py-1 text-left text-note font-medium text-strong hover:bg-blue-soft"
    >
      {c.action === "add" ? "＋" : c.action === "open" ? "↗" : "✎"} {c.label}
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
  /** 流式中的当前轮：按到达顺序的时间线（round 到达后由服务端真源替换） */
  const [live, setLive] = useState<LiveEntry[] | null>(null);
  /** 轮内插话的排队条（2026-08-25 用户拍板）：不插进对话流 —— 输入框上方一行
   *  省略显示「排队中」；轮末服务端真源会把插话按真实时序内嵌进 agent 消息
   *  segments（记录不丢），排队列清空 */
  const [pendingSays, setPendingSays] = useState<string[]>([]);
  // ---- 滚动跟随（chat-scroll.ts 的转移表；用户往上拨 = 钉住阅读位 + 显示回底浮钮）----
  const listRef = useRef<HTMLDivElement | null>(null);
  const [follow, setFollowState] = useState(true);
  const followRef = useRef(true);          // effect 闭包要读最新值，state 不保证同步
  const setFollow = (v: boolean): void => { followRef.current = v; setFollowState(v); };
  /** 平滑跳底途中忽略中间落点（不然按钮会闪一下再消失） */
  const jumpingRef = useRef(false);

  const scrollToListBottom = (smooth: boolean): void => {
    const el = listRef.current;
    if (!el) return;
    el.scrollTo({ top: el.scrollHeight, behavior: smooth ? "smooth" : "auto" });
  };

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
    // 只有本来就在底部才跟着滚；用户往上拨时阅读位置钉住（浮钮出现，见渲染）
    if (chatScroll(followRef.current, { type: "content" }).scrollToBottom) {
      scrollToListBottom(false);
    }
  }, [messages, live]);

  const onListScroll = (e: UIEvent<HTMLDivElement>): void => {
    const near = isNearBottom(e.currentTarget);
    if (jumpingRef.current) {
      if (near) jumpingRef.current = false;   // 跳底动画到达；中间落点全忽略
      else return;
    }
    if (near !== followRef.current) setFollow(near);
  };

  const jumpToBottom = (): void => {
    jumpingRef.current = true;
    setFollow(true);
    scrollToListBottom(true);
  };

  /** SSE 事件 → 时间线（chat-live.ts 的纯 reducer；规则与测试锁都在那里）：
   *  正文/思考/工具按**到达顺序**交错落位 —— 后到的思考不再插到已输出正文的上方。 */
  const applyEvent = (ev: ChatEvent): void => {
    setLive((p) => applyLiveEvent(p ?? [], ev));
  };

  /** 斜杠指令匹配（输入以 / 开头时接管回车）；返回 true = 已处理，不进 LLM */
  const runSlash = (raw: string): boolean => {
    const text = raw.trim();
    if (!text.startsWith("/")) return false;
    const name = text.slice(1).split(/\s+/)[0];
    const hit = SLASH_COMMANDS.find((c) => c.cmd === name);
    if (!hit) return false;
    if (hit.cmd === "clean") {
      void (async () => {
        setInput("");
        const ok = await cleanChat();
        if (ok) {
          setMessages([{
            role: "agent", at: Date.now() / 1000,
            text: "（上下文已清空 —— 下一轮从空白开始；memory/ 里的记忆与改进笔记都在磁盘上，不受影响）",
          }]);
          setLive(null);
        } else {
          setChatErr("clean 失败：后端不可达或对话服务未启用");
        }
      })();
    }
    return true;
  };

  const send = (): void => {
    const text = input.trim();
    if (!text) return;
    if (runSlash(text)) return;
    if (busy) {
      // 顾问运行中 = 插话（2026-08-24）：立刻排进队列（sleep 早醒 / 工具结果捎带）。
      // 显示走输入框上方排队条（2026-08-25 用户拍板：「气泡直接插进对话流」不对，
      // 要的是排队感）；轮末服务端真源把插话内嵌进 agent segments —— 双向不丢记录
      void (async () => {
        const r = await interjectChat(text);
        if (r.queued) {
          setPendingSays((q) => [...q, text]);
          setInput("");
        } else {
          setChatErr("插话没排上（" + (r.reason ?? "刚好在轮间隙") + "）—— 再按一次发送");
        }
      })();
      return;
    }
    setBusy(true);
    setChatErr(null);
    setLive([]);
    setFollow(true);   // 发出消息 = 想看回应：恢复跟随（chat-scroll 的 send 语义）
    setMessages((m) => [...m, { role: "user", text, at: Date.now() / 1000 }]);
    setInput("");
    sayChatStream(text, (ev) => {
      if (ev.type === "interject_delivered") {
        // 插话已送达模型（sleep 早醒/工具捎带的 drain 时刻）—— 排队条立刻撤；
        // 轮末 segments 会按真实时序把它内嵌进 agent 消息（记录不丢）
        setPendingSays((q) => withoutDelivered(q, ev.texts));
        return;
      }
      if (ev.type === "round") {
        if (ev.error) {
          setChatErr(ev.error);   // G7：失败理由原样显形
          setLive(null);
          setPendingSays([]);
        } else if (ev.messages) {
          setMessages(ev.messages);
          setLive(null);
          setPendingSays([]);   // 插话已内嵌进 agent segments，排队条清空
          // 改动落盘 → 清单可能过期（agent 走 REST 写，前端清单不会自己更新 ——
          // 2026-08-24 用户报「不刷新看不到最新规划」）：轮末按改动域重拉
          const areas = new Set((ev.changes ?? []).map((c) => c.area));
          if (areas.size > 0) {
            if (areas.has("plan") || areas.has("map_plan")) {
              void useQueueStore.getState().refresh();
            }
            if (areas.has("live")) void useFrames.getState().refreshSources();
          }
        }
      } else {
        applyEvent(ev);
      }
    }).catch((err: Error) => {
      setChatErr(err.message);
      setLive(null);
      setMessages((m) => [...m, { role: "agent", text: `（发送失败：${err.message}）`, at: Date.now() / 1000 }]);
    }).finally(() => {
      setBusy(false);
      setPendingSays([]);   // 流断/异常兜底：排队列不允许跨轮残留
    });
  };

  const inputPlaceholder = !api.ok
    ? "后端未连接"
    : chatReason
      ? `对话不可用：${chatReason}`
      : busy
        ? "顾问运行中 —— 可直接插话（Enter 发送，它会在下一个检查点看到）"
        : "和 agent 商量打法：读规划、试算、比战术（Enter 发送，Shift+Enter 换行）";

  return (
    <aside className="flex w-160 min-h-0 flex-1 flex-col overflow-hidden bg-panel">
      <div className="flex items-center gap-2 border-b border-l1 px-3 py-2">
        <span className="font-semibold text-strong">对话</span>
      </div>

      <div className="relative flex min-h-0 flex-1 flex-col">
        <div ref={listRef} onScroll={onListScroll}
             className="min-h-0 flex-1 space-y-3 overflow-auto p-3">
          {messages.length === 0 && !live && (
            <div className="text-note text-ghost">
              和顾问说第一句话（例：「看看默认规划，试算 300 秒，告诉我卡在哪」）。
              它能直接读改生产/地图规划、干跑试算、记忆你的偏好。
            </div>
          )}
          {messages.map((m, i) => m.nudge
            ? <NudgeBar key={i} m={m} />
            // 轮内插话的独立条目只喂 LLM（_seed_history）；显示走下一条 agent
            // 消息的 segments（按真实时序内嵌）—— 有 segments 就跳过，别重复出两条
            : m.interjection && messages[i + 1]?.segments
              ? null
              : m.role === "user"
                ? <UserMessage key={i} m={m} />
                : <AgentMessage key={i} m={m} />)}
          {/* 发话即显「思考中」：首个分片到达前 UI 不能是死屏（模型思考越久越像卡死；
              turn_start 前端不渲染、占位又在十五轮退役了 —— 2026-08-24 补回一个最小版） */}
          {live && (live.length === 0
            ? <ThinkRow text="…" running />
            : <LiveMessage entries={live} />)}
        </div>
        {/* 往上拨时出现（右下角）：点击平滑回底并恢复跟随；钉住阅读期间流式不再拽走位置 */}
        {!follow && (
          <button
            onClick={jumpToBottom}
            title="回到最新（跟随新内容）"
            className="absolute bottom-3 right-3 flex h-7 w-7 items-center justify-center rounded-full border border-l2 bg-raised text-dim shadow-md hover:bg-inset hover:text-strong"
          >
            <svg width="12" height="12" viewBox="0 0 12 12" fill="none" aria-hidden>
              <path d="M6 1.5v8M2.5 6.5 6 10l3.5-3.5" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
          </button>
        )}
      </div>

      <div className="border-t border-l1 p-2">
        {pendingSays.length > 0 && (
          <div className="mb-1.5 space-y-1">
            {pendingSays.map((t, i) => (
              <div key={i}
                   className="flex items-center gap-2 rounded-lg border border-l2 bg-inset px-2 py-1 text-note"
                   title={t}>
                <span className="shrink-0 text-blue-fg">
                  {pendingSays.length > 1 ? `排队 ${i + 1}/${pendingSays.length}` : "排队中"}
                </span>
                <span className="min-w-0 flex-1 truncate text-dim">{t}</span>
                <span className="shrink-0 text-ghost">下一个检查点送达</span>
              </div>
            ))}
          </div>
        )}
        {input.startsWith("/") && (
          <div className="mb-1 rounded-lg border border-l1 bg-inset px-2 py-1 text-note">
            {SLASH_COMMANDS
              .filter((c) => c.cmd.startsWith(input.trim().slice(1)))
              .map((c) => (
                <button
                  key={c.cmd}
                  className="flex w-full items-center gap-2 rounded px-1 py-0.5 text-left hover:bg-raised"
                  onClick={() => runSlash("/" + c.cmd)}
                  title="点击执行"
                >
                  <code className="text-mono text-blue-fg">/{c.cmd}</code>
                  <span className="text-ghost">{c.desc}</span>
                </button>
              ))}
            {SLASH_COMMANDS.every((c) => !c.cmd.startsWith(input.trim().slice(1))) && (
              <span className="text-ghost">没有这个指令（现有：{SLASH_COMMANDS.map((c) => "/" + c.cmd).join(" ")}）</span>
            )}
          </div>
        )}
        <textarea
          rows={5}
          disabled={!api.ok || chatReason !== null}
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
