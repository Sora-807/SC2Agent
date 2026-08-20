/**
 * 右侧对话栏骨架（F7 填内容）
 *
 * 形态已定（plan-frontend.md §6）：消息流 + 内联提案卡 + 待审批徽标；**不做快捷指令按钮**。
 * F1 只立骨架并把"待审批"计数接上真帧，好让提案流的位置在布局里先占住。
 */
import { useState } from "react";
import { useFrames } from "../store/frames";
import { fmtTime } from "./ui";

export function ChatDock() {
  const [open, setOpen] = useState(true);
  const proposals = useFrames((s) => s.proposals);
  const pending = proposals?.proposals.filter((p) => p.status === "待审批") ?? [];

  if (!open) {
    return (
      <button
        onClick={() => setOpen(true)}
        className="ml-2 flex w-8 shrink-0 flex-col items-center gap-2 rounded border border-neutral-800 py-2 text-xs text-neutral-500"
        title="展开对话栏"
      >
        <span>◀</span>
        <span className="[writing-mode:vertical-rl]">对话</span>
        {pending.length > 0 && (
          <span className="rounded bg-fuchsia-700 px-1 text-[10px] text-white">{pending.length}</span>
        )}
      </button>
    );
  }

  return (
    <aside className="ml-2 flex w-80 shrink-0 flex-col rounded border border-neutral-800 bg-neutral-900/40">
      <div className="flex items-center gap-2 border-b border-neutral-800 px-3 py-2">
        <span className="font-semibold">对话</span>
        {pending.length > 0 && (
          <span className="rounded bg-fuchsia-700 px-1.5 text-[11px] text-white">待审批 {pending.length}</span>
        )}
        <button className="ml-auto text-neutral-500" onClick={() => setOpen(false)} title="收起">▶</button>
      </div>

      <div className="flex-1 space-y-2 overflow-auto p-3 text-sm">
        {pending.length === 0 ? (
          <div className="text-neutral-600">暂无提案。</div>
        ) : (
          pending.map((p) => (
            <div key={p.id} className="rounded border border-fuchsia-900/60 bg-fuchsia-950/20 p-2">
              <div className="text-xs text-neutral-500">
                {p.author === "agent" ? "agent" : "你"} · {fmtTime(p.created_at)} · {p.kind}
              </div>
              <div className="mt-1 font-medium">{p.title_zh}</div>
              <div className="mt-1 text-neutral-400">{p.rationale_zh}</div>
              <ul className="mt-1 list-inside list-disc text-neutral-300">
                {p.hunks.map((h) => <li key={h.id}>{h.text_zh}</li>)}
              </ul>
              <div className="mt-2 flex gap-2">
                <button className="rounded border border-neutral-700 px-2 py-0.5 text-xs opacity-40" disabled>
                  接受
                </button>
                <button className="rounded border border-neutral-700 px-2 py-0.5 text-xs opacity-40" disabled>
                  拒绝并说明
                </button>
                <span className="self-center text-[11px] text-neutral-600">F7 接命令 API</span>
              </div>
            </div>
          ))
        )}
      </div>

      <div className="border-t border-neutral-800 p-2">
        <textarea
          rows={2}
          disabled
          placeholder="和 agent 商量打法…（F7 接入）"
          className="w-full resize-none rounded border border-neutral-800 bg-neutral-950 p-2 text-sm text-neutral-400 placeholder:text-neutral-600"
        />
      </div>
    </aside>
  );
}
