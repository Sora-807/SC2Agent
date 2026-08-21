/**
 * 命令客户端（B6）—— UI 与 agent 共用同一入口（决策 U7 / 审批红线 P4）。
 *
 * 每条命令自动带上 `based_on_seq`（R8：旧观察不得作为当前行动依据）。
 * seq 取**当前帧**的 seq —— 所以"拖回历史后下命令"会被后端 409 拒掉，这是对的：
 * 那一刻用户看的不是现在的世界。
 *
 * 409 与 400 的区别要传给用户：
 * - 409 = 世界变了（重取最新帧再试）；
 * - 400 = 请求本身不合法（原因由后端给，前端不编）。
 */
import { API_BASE } from "../store/frames";

export type CommandKind =
  | { kind: "queue"; op: "submit" | "append" | "prepend" | "clear" | "remove" | "reorder";
      body: Record<string, unknown> }
  | { kind: "workers"; body: { task: "mineral" | "gas" | "idle"; count: number } };

export interface CommandOk {
  ok: true;
  accepted_seq: number;
  detail: Record<string, unknown>;
}

export interface CommandErr {
  ok: false;
  /** stale = 观察过期（重取再试）；invalid = 请求不合法；offline = 没有会话/后端 */
  reason: "stale" | "invalid" | "offline" | "network";
  message: string;
  current_seq?: number;
}

export type CommandResult = CommandOk | CommandErr;

export async function sendCommand(cmd: CommandKind, basedOnSeq: number): Promise<CommandResult> {
  const url =
    cmd.kind === "queue"
      ? new URL("/api/commands/queue/" + cmd.op, API_BASE)
      : new URL("/api/commands/workers", API_BASE);
  const body = { based_on_seq: basedOnSeq, ...cmd.body };
  let res: Response;
  try {
    res = await fetch(url.toString(), {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    });
  } catch (err) {
    return { ok: false, reason: "network", message: "连不上后端：" + (err as Error).message };
  }
  if (res.ok) return (await res.json()) as CommandOk;

  const payload = (await res.json().catch(() => ({}))) as { detail?: unknown };
  const detail = payload.detail;
  if (res.status === 409) {
    if (detail && typeof detail === "object" && "current_seq" in detail) {
      const d = detail as { reason: string; current_seq: number };
      return { ok: false, reason: "stale", message: d.reason, current_seq: d.current_seq };
    }
    return { ok: false, reason: "offline", message: String(detail ?? "没有运行中的会话") };
  }
  return { ok: false, reason: "invalid", message: typeof detail === "string" ? detail : JSON.stringify(detail) };
}

/** 会话控制：起/停/单步（单步是"不自动推进"时的调试入口）。
 *  `driver`：sim = 沙盒（子进程假世界）；sc2 = 真机（会启动真实 SC2 游戏）。 */
export async function sessionAction(
  action: "start" | "stop" | "tick",
  opts: { autotick?: boolean; count?: number; driver?: "sim" | "sc2" } = {},
): Promise<Record<string, unknown> | null> {
  const url = new URL("/api/session/" + action, API_BASE);
  if (action === "start" && opts.autotick === false) url.searchParams.set("autotick", "false");
  if (action === "start" && opts.driver) url.searchParams.set("driver", opts.driver);
  if (action === "tick" && opts.count) url.searchParams.set("count", String(opts.count));
  try {
    const res = await fetch(url.toString(), { method: "POST" });
    if (!res.ok) return null;
    return (await res.json()) as Record<string, unknown>;
  } catch {
    return null;
  }
}
