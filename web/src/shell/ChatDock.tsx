/**
 * 右侧对话栏（F7）—— **重形态**：消息流 + 内联提案卡 + 待审批收件箱。
 * 刻意**不做快捷指令按钮**（用户已确认）：这不是观战器，是和 agent 商量打法的地方。
 *
 * 提案卡只做"摘要 + 打开"；完整 diff 与审批在主区（`ProposalReview`）——
 * 双投影对比图在 320px 宽的侧栏里没法看。
 *
 * B10 之前 agent 还没接上，所以下方输入框是禁用的；但**提案通道已经通了**：
 * 后端 `POST /api/proposals` 已经能收，UI 已经能审批。agent 接上时不用改这里。
 */
import { useEffect, useState } from "react";
import { listProposals } from "../api/proposals";
import { useFrames } from "../store/frames";
import { fmtTime } from "./ui";
import type { Proposal } from "../contract";

const STATUS_TONE: Record<string, string> = {
  "待审批": "border-fuchsia-900/60 bg-fuchsia-950/20",
  "已接受": "border-emerald-900/60 bg-emerald-950/20",
  "部分接受": "border-sky-900/60 bg-sky-950/20",
  "已拒绝": "border-neutral-800 bg-neutral-900/40",
  "已失效": "border-amber-900/60 bg-amber-950/20",
};

export function ChatDock(props: {
  selected: string | null;
  onOpen: (id: string | null) => void;
}) {
  const [open, setOpen] = useState(true);
  const [showAll, setShowAll] = useState(false);
  const frameProposals = useFrames((s) => s.proposals);
  const api = useFrames((s) => s.api);
  const sourceKind = useFrames((s) => s.sourceKind);
  const [fetched, setFetched] = useState<Proposal[] | null>(null);

  // 帧里的提案是权威（live 会推）；夹具场景下帧里也有样本。
  // 但用户可能在看本地夹具而后端另有提案 —— 那种情况下轮询一次后端补上。
  useEffect(() => {
    if (!api.ok || sourceKind === "api") return;
    let alive = true;
    listProposals().then((rows) => alive && setFetched(rows)).catch(() => undefined);
    return () => { alive = false; };
  }, [api.ok, sourceKind, frameProposals]);

  const rows: Proposal[] = frameProposals?.proposals?.length
    ? frameProposals.proposals
    : fetched ?? [];
  const pending = rows.filter((p) => p.status === "待审批");
  const shown = showAll ? rows : pending;

  if (!open) {
    return (
      <button
        onClick={() => setOpen(true)}
        className="ml-2 flex w-8 shrink-0 flex-col items-center gap-2 rounded border border-neutral-800 py-2 text-xs text-faint"
        title="展开对话栏"
      >
        <span>◀</span>
        <span className="[writing-mode:vertical-rl]">对话</span>
        {pending.length > 0 && (
          <span className="rounded bg-fuchsia-700 px-1 text-note text-white">{pending.length}</span>
        )}
      </button>
    );
  }

  return (
    <aside className="ml-2 flex w-80 shrink-0 flex-col rounded border border-neutral-800 bg-neutral-900/40">
      <div className="flex items-center gap-2 border-b border-neutral-800 px-3 py-2">
        <span className="font-semibold">对话</span>
        {pending.length > 0 && (
          <span className="rounded bg-fuchsia-700 px-1.5 text-note text-white">
            待审批 {pending.length}
          </span>
        )}
        <button
          className="ml-auto text-note text-faint hover:text-neutral-300"
          onClick={() => setShowAll((v) => !v)}
        >{showAll ? "只看待审批" : "看全部"}</button>
        <button className="text-faint" onClick={() => setOpen(false)} title="收起">▶</button>
      </div>

      <div className="flex-1 space-y-2 overflow-auto p-3 text-sm">
        {shown.length === 0 ? (
          <div className="text-ghost">
            {rows.length === 0
              ? "暂无提案。agent 接上后（B10）会在这里推草稿，你审批。"
              : "没有待审批的提案（点上面「看全部」查看历史）。"}
          </div>
        ) : (
          shown.map((p) => (
            <button
              key={p.id}
              onClick={() => props.onOpen(props.selected === p.id ? null : p.id)}
              className={"w-full rounded border p-2 text-left " +
                (STATUS_TONE[p.status] ?? "border-neutral-800") +
                (props.selected === p.id ? " ring-1 ring-neutral-400" : "")}
            >
              <div className="flex items-baseline gap-1 text-note text-faint">
                <span>{p.author === "agent" ? "agent" : "你"}</span>
                <span>· {fmtTime(p.created_at)}</span>
                <span>· {p.kind}</span>
                <span className="ml-auto">{p.status}</span>
              </div>
              <div className="mt-1 font-medium text-neutral-100">{p.title_zh}</div>
              <div className="mt-1 line-clamp-3 text-dim">{p.rationale_zh}</div>
              <div className="mt-1 flex items-center gap-2 text-note">
                <span className="text-ghost">{p.hunks.length} 条改动</span>
                {p.validation?.ok === false && (
                  <span className="text-red-400">校验未通过</span>
                )}
                {p.preview?.kind === "projection_pair" && (
                  <span className="text-emerald-500">可看双投影</span>
                )}
                <span className="ml-auto text-faint">
                  {props.selected === p.id ? "收起 ▾" : "打开 ▸"}
                </span>
              </div>
            </button>
          ))
        )}
      </div>

      <div className="border-t border-neutral-800 p-2">
        <textarea
          rows={2}
          disabled
          placeholder={api.ok
            ? "和 agent 商量打法…（agent 接入见 B10；提案通道已通，后端已能收提案）"
            : "后端未连接"}
          className="w-full resize-none rounded border border-neutral-800 bg-neutral-950 p-2 text-sm text-dim placeholder:text-ghost"
        />
        <div className="mt-1 text-note text-ghost">
          按设计不做快捷指令按钮：agent 推草稿、你审批。
        </div>
      </div>
    </aside>
  );
}
