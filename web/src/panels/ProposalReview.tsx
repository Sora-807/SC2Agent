/**
 * 提案审批（F7）—— 三种 diff 视图 + 逐条接受 + 拒绝附理由。
 *
 * 三条纪律直接对应 §6 的红线：
 * - **P2** 校验不通过的提案：接受按钮禁用，但**内容与错误都显示**（agent 要学、用户要诊断）；
 * - **P5** 已失效的提案：不能接受，并说明"它基于的世界已经不在了"；
 * - **P3** 拒绝**必须**填理由，理由回流给 agent（否则它会重复推同一个提案）。
 *
 * 三种 diff 按 kind 分派；后端只对 `production_queue` 能算预览，
 * 其余两种显示**后端给的原因**而不是假装能画（不给假界面）。
 */
import { useEffect, useState } from "react";
import { acceptProposal, fetchPreview, rejectProposal, type ProjectionPair } from "../api/proposals";
import { ProjectionPairChart } from "../charts/ProjectionPairChart";
import { canReject, reviewGate } from "./proposal-gate";
import { Empty, fmtTime } from "../shell/ui";
import { useFrames } from "../store/frames";
import type { Proposal } from "../contract";

const STATUS_TONE: Record<string, string> = {
  "待审批": "bg-fuchsia-900/60 text-fuchsia-200",
  "已接受": "bg-emerald-900/60 text-emerald-200",
  "部分接受": "bg-sky-900/60 text-sky-200",
  "已拒绝": "bg-neutral-800 text-neutral-400",
  "已失效": "bg-amber-900/50 text-amber-300",
};

export function ProposalReview(props: { proposal: Proposal; onDone: () => void }) {
  const p = props.proposal;
  const catalog = useFrames((s) => s.catalog);
  const position = useFrames((s) => s.position);
  const [picked, setPicked] = useState<Set<string>>(new Set(p.hunks.map((h) => h.id)));
  const [pair, setPair] = useState<ProjectionPair | null>(null);
  const [previewErr, setPreviewErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [reason, setReason] = useState("");
  const [error, setError] = useState<string | null>(null);

  const zhOf = (id: string | null): string =>
    (id ? catalog?.entries.find((e) => e.stable_id === id)?.display_name_zh ?? id : "—");

  const gate = reviewGate(p);
  const valid = p.validation?.ok === true;
  const stale = gate.ok === false && gate.why === "stale";
  const canAct = gate.ok;

  useEffect(() => {
    if (p.preview?.kind !== "projection_pair") return;
    setPair(null);
    setPreviewErr(null);
    fetchPreview(p.id).then(setPair).catch((e: Error) => setPreviewErr(e.message));
  }, [p.id, p.preview?.kind]);

  const act = async (fn: () => Promise<unknown>): Promise<void> => {
    setBusy(true);
    setError(null);
    try {
      await fn();
      props.onDone();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const anchorAge = p.anchor ? position - p.anchor.game_time : null;

  return (
    <div className="space-y-3">
      <div className="rounded border border-neutral-800 bg-neutral-900/40 p-3">
        <div className="flex flex-wrap items-baseline gap-2">
          <span className={"rounded px-1.5 text-[11px] " + (STATUS_TONE[p.status] ?? "bg-neutral-800")}>
            {p.status}
          </span>
          <h2 className="font-semibold text-neutral-100">{p.title_zh}</h2>
          <span className="text-[11px] text-neutral-500">
            {p.author === "agent" ? "agent 提出" : "你提出"} · {fmtTime(p.created_at)} · {p.kind}
          </span>
        </div>
        <div className="mt-2 rounded bg-neutral-950/60 p-2 text-neutral-300">
          <span className="text-neutral-500">理由：</span>{p.rationale_zh}
        </div>
        {p.anchor && (
          <div className="mt-1 text-[11px] text-neutral-500">
            基于 seq {p.anchor.seq}（{fmtTime(p.anchor.game_time)}）
            {anchorAge !== null && anchorAge > 0 && ` · 已过去 ${anchorAge.toFixed(0)}s`}
            {stale && <span className="ml-1 text-amber-400">
              —— 它基于的世界已经不在了，不能盲接受
            </span>}
          </div>
        )}
        {!valid && (
          <div className="mt-2 rounded border border-red-900 bg-red-950/40 p-2 text-red-300">
            <div className="font-medium">校验未通过 —— 不可接受，但保留可见（便于诊断与让 agent 学）</div>
            <ul className="mt-1 list-inside list-disc">
              {(p.validation?.errors ?? []).map((e, i) => <li key={i}>{e.text_zh}</li>)}
            </ul>
          </div>
        )}
        {p.decision && (
          <div className="mt-2 text-[11px] text-neutral-400">
            决定于 {fmtTime(p.decision.at)}
            {p.decision.accepted_hunks.length > 0 &&
              ` · 接受了 ${p.decision.accepted_hunks.join("、")}`}
            {p.decision.comment_zh && ` · 理由：${p.decision.comment_zh}`}
          </div>
        )}
      </div>

      <div className="rounded border border-neutral-800 bg-neutral-900/40 p-3">
        <h3 className="mb-2 font-semibold text-neutral-200">改动（可逐条选择）</h3>
        {p.hunks.length === 0 ? <Empty text="这条提案没有任何改动" /> : (
          <ul className="space-y-1">
            {p.hunks.map((h) => (
              <li key={h.id} className="flex items-start gap-2">
                <input
                  type="checkbox" className="mt-1"
                  disabled={!canAct}
                  checked={picked.has(h.id)}
                  onChange={(e) => setPicked((s) => {
                    const next = new Set(s);
                    if (e.target.checked) next.add(h.id);
                    else next.delete(h.id);
                    return next;
                  })}
                />
                <div>
                  <div className="text-neutral-200">{h.text_zh}</div>
                  <div className="text-[10px] text-neutral-600">
                    {h.id} · {h.kind} · {JSON.stringify(h.payload)}
                  </div>
                </div>
              </li>
            ))}
          </ul>
        )}
      </div>

      <PreviewBlock proposal={p} pair={pair} error={previewErr} zhOf={zhOf} />

      {!canAct && (
        <div className="rounded border border-neutral-800 bg-neutral-900/40 px-3 py-2 text-[12px] text-neutral-400">
          不能接受：{(gate as { reason: string }).reason}
        </div>
      )}

      {canAct && (
        <div className="rounded border border-neutral-800 bg-neutral-900/40 p-3 space-y-2">
          <div className="flex flex-wrap items-center gap-2">
            <button
              disabled={busy || picked.size === 0}
              className="rounded border border-emerald-700 bg-emerald-900/40 px-3 py-1 text-emerald-200 disabled:opacity-40"
              onClick={() => void act(() => acceptProposal(
                p.id,
                picked.size === p.hunks.length ? undefined : [...picked],
                reason || undefined))}
            >
              {picked.size === p.hunks.length ? "接受全部" : `接受选中的 ${picked.size} 条`}
            </button>
            <button
              disabled={busy || !canReject(reason)}
              className="rounded border border-red-800 bg-red-950/40 px-3 py-1 text-red-300 disabled:opacity-40"
              title={reason.trim() ? "" : "拒绝必须填理由（理由会回流给 agent）"}
              onClick={() => void act(() => rejectProposal(p.id, reason))}
            >拒绝</button>
            <span className="text-[11px] text-neutral-500">
              接受 = 走与 agent 相同的命令路径（不开后门）
            </span>
          </div>
          <textarea
            rows={2}
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            placeholder="拒绝理由（必填）／接受时的备注（可选）—— 理由会回流给 agent，否则它会重复推同一个提案"
            className="w-full resize-none rounded border border-neutral-800 bg-neutral-950 p-2 text-sm placeholder:text-neutral-600"
          />
          {error && <div className="text-red-400">{error}</div>}
        </div>
      )}
    </div>
  );
}

/** 三种 diff 按 kind 分派；后端算不了的就说清原因，不假装能画 */
function PreviewBlock(props: {
  proposal: Proposal;
  pair: ProjectionPair | null;
  error: string | null;
  zhOf: (id: string | null) => string;
}) {
  const kind = props.proposal.preview?.kind;
  return (
    <div className="rounded border border-neutral-800 bg-neutral-900/40 p-3">
      <h3 className="mb-2 font-semibold text-neutral-200">
        预览 · {kind === "projection_pair" ? "双投影对比（接受前先看未来）"
          : kind === "map_overlay" ? "地图叠加"
          : kind === "graph_diff" ? "策略图 diff" : "无"}
      </h3>
      {kind === "projection_pair" ? (
        props.error ? (
          <div className="text-amber-400">算不出预览：{props.error}</div>
        ) : props.pair ? (
          <ProjectionPairChart current={props.pair.current} proposed={props.pair.proposed} />
        ) : (
          <div className="text-neutral-500">正在算两条未来…</div>
        )
      ) : kind === "map_overlay" ? (
        <div className="text-neutral-500">
          地图叠加要 F9 的 `map_plan` patch 模型 —— 后端目前也不能应用这类提案。
        </div>
      ) : kind === "graph_diff" ? (
        <div className="text-neutral-500">
          策略图 diff 要 F9 的 AST 编辑器 —— 而 flow 提交必须 validate + compile（R6），
          且 live 不能编辑 Strategy（R5）。
        </div>
      ) : (
        <div className="text-neutral-500">
          这类提案没有可视预览（后端的 validation 里通常写了为什么不能应用）。
        </div>
      )}
    </div>
  );
}