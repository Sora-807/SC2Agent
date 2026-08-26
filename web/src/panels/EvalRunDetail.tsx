/**
 * 评测 run 详情（PLAN-EVAL-FRONTEND 批 B）：一次运行的全量指标——
 * 概要 / 判定明细 / 工具序列 / 提案与变更 / 回复与推理 / 工作区 / 会话终态。
 * 数据 = GET /api/eval/runs/{run_dir}（result.json/grades.json 直读，不重算）；
 * 提示词全文懒加载（点 prompt_hash 才取）；对话只给条数 + 按需摘要（量大不内联）。
 */
import { useEffect, useState } from "react";
import { fetchRun, fetchRunPrompt, type RunDetail } from "../api/eval";
import { Card, Empty } from "../shell/ui";
import { ChangeChip } from "../shell/change-chip";

export function EvalRunDetail(props: {
  runDir: string;
  onBack: () => void;
  onOpenProject?: (id: string) => void;
}) {
  const [d, setD] = useState<RunDetail | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [msgs, setMsgs] = useState<{ role: string | null; content: string }[] | null>(null);
  const [prompt, setPrompt] = useState<string | null>(null);
  const [promptErr, setPromptErr] = useState<string | null>(null);

  useEffect(() => {
    setD(null);
    setErr(null);
    setMsgs(null);
    setPrompt(null);
    setPromptErr(null);
    fetchRun(props.runDir).then(setD).catch((e: Error) => setErr(e.message));
  }, [props.runDir]);

  const loadMessages = (): void => {
    if (msgs || !d) return;
    fetchRun(props.runDir, true)
      .then((r) => setMsgs(r.messages))
      .catch((e: Error) => setErr(e.message));
  };
  const loadPrompt = (): void => {
    if (prompt || promptErr) return;
    fetchRunPrompt(props.runDir)
      .then(setPrompt)
      .catch((e: Error) => setPromptErr(e.message));
  };

  if (err) {
    return <Card title="运行详情"><Empty text={"读取失败：" + err} /></Card>;
  }
  if (!d) {
    return <Card title="运行详情"><Empty text="加载 run 指标…" /></Card>;
  }

  const m = d.meta;
  const passed = d.grades.filter((g) => g.passed === true
    || (g.passed === null && g.score !== null && g.score >= 3)).length;
  const row = d.index_row;

  return (
    <div className="space-y-2 p-1">
      <div className="flex flex-wrap items-baseline gap-2">
        <button className="btn btn-ghost" onClick={props.onBack}>← 返回总览</button>
        {row ? (
          <span className="text-note text-dim">
            {row.ts} · 批次 {row.label} ·{" "}
            <span className={props.onOpenProject ? "cursor-pointer underline" : ""}
                  onClick={props.onOpenProject ? () => props.onOpenProject?.(row.project) : undefined}>
              {row.project}
            </span>{" "}
            run{row.run_no}
          </span>
        ) : (
          <span className="text-note text-[color:var(--warn-fg)]">
            未入账 run（批次被中断：index 只在批末 append，数据在盘上）
          </span>
        )}
      </div>

      <Card title="概要">
        <div className="flex flex-wrap gap-x-4 gap-y-1 text-label">
          <span className={m.outcome === "done" ? "" : "text-[color:var(--warn-fg)]"}>
            {m.outcome ?? "?"}
          </span>
          <span className={passed === d.grades.length ? "" : "text-[color:var(--err-fg)]"}>
            ✅ {passed}/{d.grades.length} 轴过
          </span>
          <span className="text-dim">{m.duration_s ?? "?"}s</span>
          <span className="text-dim">tokens in {m.input_tokens ?? "?"} / out {m.output_tokens ?? "?"}</span>
          <span className="text-dim">{m.llm_model || "?"}</span>
        </div>
        <div className="mt-1 flex flex-wrap items-baseline gap-x-4 gap-y-1 text-note">
          <span className="text-faint">prompt_hash</span>
          <button className="cursor-pointer font-mono text-dim underline"
                  onClick={loadPrompt} title="点击加载提示词全文快照（D16）">
            {m.prompt_hash ?? "?"}
          </button>
          <span className="text-faint">seed_hash</span>
          <span className="font-mono text-dim">{m.seed_hash ?? "?"}</span>
        </div>
        {promptErr && <div className="mt-1 text-note text-[color:var(--warn-fg)]">{promptErr}</div>}
        {prompt && (
          <pre className="mt-1 max-h-96 overflow-y-auto whitespace-pre-wrap rounded bg-inset p-2 text-label text-dim">
            {prompt}
          </pre>
        )}
      </Card>

      <Card title="判定明细" help="确定性轴 ✅/❌，LLM 判官轴 ⭐（0-5，≥3 算过）">
        <table className="w-full text-label">
          <tbody>
            {d.grades.map((g, i) => (
              <tr key={i} className="border-t border-l1">
                <td className="w-16 py-1 align-top">
                  {g.passed !== null
                    ? (g.passed ? "✅" : "❌")
                    : g.score !== null ? "⭐".repeat(Math.max(1, Math.round(g.score))) : "—"}
                </td>
                <td className="w-36 py-1 align-top text-dim">[{g.axis}] {g.grader}</td>
                <td className="py-1 text-dim">{g.reason_zh}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>

      <Card title={"工具序列（" + d.tool_calls.length + "）"}>
        {d.tool_calls.length === 0 ? <Empty text="没有工具调用" /> : (
          <div className="space-y-1">
            {d.tool_calls.map((t, i) => (
              <details key={i}>
                <summary className="cursor-pointer text-label text-dim">
                  <span className="font-mono text-strong">{t.tool ?? "?"}</span>
                  {" "}· {t.duration_ms ?? "?"}ms · turn {t.turn_no ?? "?"}
                  {t.result_preview ? " · " + t.result_preview.split("\n")[0]?.slice(0, 80) : ""}
                </summary>
                {t.args !== undefined && t.args !== null && (
                  <pre className="mt-1 max-h-40 overflow-y-auto whitespace-pre-wrap rounded bg-inset p-2 text-label text-dim">
                    {typeof t.args === "string" ? t.args : JSON.stringify(t.args, null, 1)}
                  </pre>
                )}
                {t.result_preview && (
                  <pre className="mt-1 max-h-60 overflow-y-auto whitespace-pre-wrap rounded bg-inset p-2 text-label text-dim">
                    {t.result_preview}
                  </pre>
                )}
              </details>
            ))}
          </div>
        )}
      </Card>

      <Card title={"提案与变更（" + d.proposals.length + " / " + d.changes.length + "）"}>
        {d.proposals.length === 0 && d.changes.length === 0 ? (
          <Empty text="没有提案也没有变更" />
        ) : (
          <div className="space-y-2">
            {d.proposals.map((p) => (
              <div key={p.id} className="rounded border border-l1 p-2">
                <div className="flex flex-wrap items-baseline gap-2">
                  <span className={(p.validation ?? { ok: false }).ok
                    ? "text-strong" : "text-[color:var(--err-fg)]"}>
                    {(p.validation ?? { ok: false }).ok ? "✓" : "✗"} {p.title_zh ?? p.id}
                  </span>
                  {p.rationale_zh && <span className="text-note text-dim">理由：{p.rationale_zh}</span>}
                </div>
                {(p.validation && !p.validation.ok) && (
                  <div className="mt-1 text-note text-[color:var(--err-fg)]">
                    校验未通过{p.validation.problems?.length ? "：" + p.validation.problems.join("；") : ""}
                  </div>
                )}
                <div className="mt-1 space-y-0.5">
                  {p.hunks.map((h) => (
                    <div key={h.id} className="text-note text-dim">
                      · {h.text_zh ?? h.kind}
                      {h.payload?.item && (
                        <span className="font-mono">（{h.payload.item.op} {h.payload.item.type}）</span>
                      )}
                    </div>
                  ))}
                </div>
              </div>
            ))}
            {d.changes.length > 0 && (
              <div className="space-y-1">
                {d.changes.map((c, i) => <ChangeChip key={i} c={c} />)}
              </div>
            )}
          </div>
        )}
      </Card>

      <Card title="回复与推理"
            help={"思考过程 " + d.reasoning.length + " 段"
              + (m.reasoning_clipped ? `（${m.reasoning_clipped} 段超 12k 护栏被截）` : "")}>
        <pre className="whitespace-pre-wrap text-body text-strong">{d.final_text || "（空）"}</pre>
        {d.reasoning.map((r, i) => (
          <details key={i} className="mt-1">
            <summary className="cursor-pointer text-note text-faint">
              思考 {i + 1} · {r.slice(0, 60)}…
            </summary>
            <pre className="mt-1 max-h-96 overflow-y-auto whitespace-pre-wrap rounded bg-inset p-2 text-label text-dim">
              {r}
            </pre>
          </details>
        ))}
      </Card>

      <Card title={"工作区写入（" + Object.keys(d.workspace).length + "）"}>
        {Object.keys(d.workspace).length === 0 ? (
          <Empty text="没写文件" />
        ) : (
          <div className="grid grid-cols-2 gap-x-4 text-label">
            {Object.entries(d.workspace).map(([p, n]) => (
              <div key={p} className="flex justify-between gap-2 border-t border-l1 py-0.5">
                <span className="break-all font-mono text-dim">{p}</span>
                <span className="shrink-0 tabular-nums text-faint">{n}B</span>
              </div>
            ))}
          </div>
        )}
      </Card>

      <Card title="会话终态">
        <div className="text-label text-dim">
          {d.session
            ? `${d.session.state ?? "?"} · 游戏时间 ${d.session.game_time ?? "?"}s · ${d.session.alive ? "存活" : "已结束"}`
            : "无会话（单轮离线场景或未连接）"}
        </div>
      </Card>

      <Card title="对话记录"
            help="全量在盘（traces 的 messages.jsonl）；这里按需加载 400 字/条摘要">
        {msgs ? (
          <div className="space-y-1">
            {msgs.map((mm, i) => (
              <div key={i} className="text-label">
                <span className="mr-1 font-mono text-faint">{mm.role ?? "?"}</span>
                <span className="text-dim">{mm.content}</span>
              </div>
            ))}
          </div>
        ) : (
          <button className="btn btn-ghost" onClick={loadMessages}>
            加载摘要（{d.messages_count} 条消息）
          </button>
        )}
      </Card>
    </div>
  );
}
