/**
 * 评测页：eval 管线的前端面 —— 场景注册表 + 跑批按钮 + 运行记录 + 钻取
 * （PLAN-EVAL-FRONTEND 批 A：项目详情）。
 *
 * 跑批两条入口（同一份归档 runtime/eval/）：本页「跑评测」按钮（POST /api/eval/run
 * 后台跑 + 轮询 job 进度）与 CLI（uv run python -m eval.run）。
 * 钻取路由：`#/eval?project=<id>`——hash 必须走 URLSearchParams 构造（值里的
 * `+` 会被 query 解码成空格，字符串拼接会坏）。
 */
import { useEffect, useState } from "react";
import { EvalProjectDetail } from "../panels/EvalProjectDetail";
import { fetchOverview, type Overview } from "../api/eval";
import { Card, Empty } from "../shell/ui";
import { API_BASE, useFrames } from "../store/frames";

/** 钻取导航：query 参数走 URLSearchParams（+ → %2B，解码还原），禁字符串拼接 */
export function evalHash(params: Record<string, string>): string {
  const q = new URLSearchParams(params);
  return `#/eval?${q.toString()}`;
}

export function EvalPage(props: { projectId?: string | null }) {
  const apiOk = useFrames((s) => s.api.ok);
  const [ov, setOv] = useState<Overview | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [runsN, setRunsN] = useState(3);
  const [starting, setStarting] = useState(false);

  const refresh = (): void => {
    fetchOverview()
      .then(setOv)
      .catch((e: Error) => setErr(e.message));
  };

  useEffect(() => {
    if (!apiOk) return;
    refresh();
  }, [apiOk]);

  // job 进行中：3s 轮询直到终态
  useEffect(() => {
    if (ov?.job?.state !== "running") return;
    const t = setInterval(refresh, 3000);
    return () => clearInterval(t);
  }, [ov?.job?.state]);

  const startRun = (): void => {
    setStarting(true);
    fetch(new URL("/api/eval/run", API_BASE).toString(), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ runs: runsN }),
    })
      .then((r) => {
        if (!r.ok) return r.json().then((b) => { throw new Error(b?.detail || "HTTP " + r.status); });
        return r.json();
      })
      .then(() => refresh())
      .catch((e: Error) => setErr(e.message))
      .finally(() => setStarting(false));
  };

  if (!apiOk) {
    return (
      <Card title="评测">
        <Empty text="评测记录存在后端（python tools/serve_api.py）—— 先连后端；CLI 跑法：uv run python -m eval.run" />
      </Card>
    );
  }

  // 项目详情钻取：overview 仍要拉（底部的「该项目的运行记录」从 runs 过滤）
  if (props.projectId) {
    return (
      <EvalProjectDetail
        id={props.projectId}
        runs={(ov?.runs ?? []).filter((r) => r.project === props.projectId)}
        onBack={() => { window.location.hash = "#/eval"; }} />
    );
  }

  if (err) return <Card title="评测"><Empty text={"读取失败：" + err} /></Card>;
  if (!ov) return <Card title="评测"><Empty text="加载评测概览…" /></Card>;

  const passOf = (r: { passed: number | null; axes: number | null }): string =>
    r.passed == null || r.axes == null ? "—" : `${r.passed}/${r.axes}`;
  const job = ov.job;
  const goProject = (id: string): void => {
    window.location.hash = evalHash({ project: id });
  };

  return (
    <div className="space-y-2 p-1">
      <Card title="跑评测"
            help="后台跑批（分钟级：每场景 N 次真 LLM 对话 + 评分），跑完记录自动出现在下方。与 CLI（同一份归档）等价。">
        <div className="flex flex-wrap items-center gap-2">
          <label className="text-label text-dim">
            每场景次数
            <input type="number" min={1} max={5} value={runsN}
                   onChange={(e) => setRunsN(Math.max(1, Math.min(5, Number(e.target.value) || 3)))}
                   className="ml-1 w-14 rounded border border-l1 bg-inset px-1 py-0.5 text-strong" />
          </label>
          <button className="btn btn-ok" disabled={starting || job?.state === "running"}
                  onClick={startRun}>
            {job?.state === "running" ? "跑批进行中…" : `跑全部 ${ov.scenarios.length} 个场景`}
          </button>
          <span className="text-note text-faint">CLI 等价：{ov.run_howto}</span>
        </div>
        {job && (
          <div className={"mt-2 rounded border p-2 text-label "
            + (job.state === "error" ? "border-[color:var(--err-fg)]" : "border-l1")}>
            <div className="flex items-center gap-2">
              <span className={job.state === "running" ? "text-[color:var(--warn-fg)]"
                : job.state === "error" ? "text-[color:var(--err-fg)]" : ""}>
                {job.state === "running" ? "⏳ 跑批中" : job.state === "done" ? "✓ 完成" : "✗ 出错"}
              </span>
              <span className="text-dim">{job.label} × {job.runs}</span>
              {job.state === "running" && (
                <span className="text-note text-faint">每 3s 自动刷新进度</span>
              )}
              {job.error && <span className="text-[color:var(--err-fg)]">{job.error}</span>}
              {job.report && <span className="text-note text-faint">报告：{job.report}</span>}
            </div>
            {job.log.length > 0 && (
              <pre className="mt-1 max-h-40 overflow-y-auto whitespace-pre-wrap rounded bg-inset p-2 text-note text-dim">
                {job.log.slice(-12).join("\n")}
              </pre>
            )}
          </div>
        )}
      </Card>
      <Card title={"场景注册表（" + ov.scenarios.length + "）"}
            help="一个场景 = fixture + 任务 + runner + 评分器组合。点行看完整构成；加场景在 eval/scenarios/ 下写（有 add-eval-scenario skill）。">
        <table className="w-full text-label">
          <thead>
            <tr className="text-faint">
              <th className="text-left font-normal">场景 id</th>
              <th className="text-left font-normal">标签</th>
              <th className="text-left font-normal">评分器</th>
              <th className="text-right font-normal">默认 N</th>
              <th className="text-left font-normal">判官</th>
            </tr>
          </thead>
          <tbody>
            {ov.scenarios.map((s) => (
              <tr key={s.id} className="cursor-pointer border-t border-l1 hover:bg-inset"
                  onClick={() => goProject(s.id)}>
                <td className="py-1 font-mono">{s.id}</td>
                <td className="text-dim">{s.tags.join(" / ")}</td>
                <td className="text-dim">{s.graders.join(" + ")}</td>
                <td className="text-right">{s.runs ?? "3"}</td>
                <td className="text-dim">{s.judge_model ?? "默认"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>
      <Card title={"运行记录（" + ov.runs.length + "，最新在前）"}
            right={<span className="text-note text-faint">跑法：{ov.run_howto}</span>}>
        {ov.runs.length === 0 ? (
          <Empty text="还没有运行记录 —— 上面「跑评测」或 CLI 跑一次就会出现" />
        ) : (
          <table className="w-full text-label">
            <thead>
              <tr className="text-faint">
                <th className="text-left font-normal">时间</th>
                <th className="text-left font-normal">批次</th>
                <th className="text-left font-normal">场景</th>
                <th className="text-right font-normal">#</th>
                <th className="text-left font-normal">结果</th>
                <th className="text-right font-normal">通过</th>
                <th className="text-left font-normal">失败项</th>
                <th className="text-left font-normal">模型</th>
              </tr>
            </thead>
            <tbody>
              {ov.runs.map((r, i) => (
                <tr key={r.run_dir + i} className="border-t border-l1">
                  <td className="py-1 text-dim">{r.ts}</td>
                  <td className="text-dim">{r.label}</td>
                  <td className="font-mono">{r.project}</td>
                  <td className="text-right">{r.run_no}</td>
                  <td className={r.outcome === "done" ? "" : "text-[color:var(--warn-fg)]"}>
                    {r.outcome}
                  </td>
                  <td className={"text-right "
                    + (r.failed_graders?.length ? "text-[color:var(--err-fg)]" : "")}>
                    {passOf(r)}
                  </td>
                  <td className="text-[color:var(--err-fg)]">{r.failed_graders?.join("；")}</td>
                  <td className="text-dim">{r.llm_model}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        <div className="mt-1 text-note text-ghost">
          记录与完整报告在 {ov.eval_root}（每次运行一个目录 + index.jsonl 清单）
        </div>
      </Card>
    </div>
  );
}
