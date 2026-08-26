/**
 * 评测项目详情（PLAN-EVAL-FRONTEND 批 A）：一个评测项目的完整构成——
 * 任务 / 局面（fixture）/ 回路（runner）/ 评分器（每个轴期望什么）。
 * 数据 = GET /api/eval/projects/{id}（eval/describe.py 的 introspection）——
 * 前端零硬编码，加场景即现。底部挂该项目的运行记录（overview.runs 过滤）。
 */
import { useEffect, useState } from "react";
import { fetchProject, type EvalComponent, type EvalRun, type ProjectDetail } from "../api/eval";
import { Card, Empty } from "../shell/ui";

export function EvalProjectDetail(props: {
  id: string;
  runs: EvalRun[];
  onBack: () => void;
  onOpenRun?: (runDir: string) => void;
}) {
  const [d, setD] = useState<ProjectDetail | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    setD(null);
    setErr(null);
    fetchProject(props.id).then(setD).catch((e: Error) => setErr(e.message));
  }, [props.id]);

  if (err) {
    return <Card title={"项目 " + props.id}><Empty text={"读取失败：" + err} /></Card>;
  }
  if (!d) {
    return <Card title={"项目 " + props.id}><Empty text="加载项目构成…" /></Card>;
  }

  return (
    <div className="space-y-2 p-1">
      <div className="flex flex-wrap items-baseline gap-2">
        <button className="btn btn-ghost" onClick={props.onBack}>← 返回总览</button>
        <span className="font-mono text-body text-strong">{d.id}</span>
        <span className="text-note text-dim">{d.tags.join(" / ")}</span>
        <span className="text-note text-faint">默认跑 {d.runs ?? 3} 次</span>
        <span className="text-note text-faint">判官：{d.judge_model ?? "默认（agent 同款）"}</span>
      </div>

      <Card title="任务（给 agent 的指令）">
        <div className="text-body text-strong">{d.task.text}</div>
        {d.task.note && <div className="mt-1 text-note text-dim">验什么：{d.task.note}</div>}
        <div className="mt-1 text-note text-faint">
          轮数上限：{d.task.max_turns ?? "（用 runner 默认）"}
        </div>
      </Card>

      <Card title="局面（fixture）"
            help="run 前把世界带到起点：setup_fn 造对局局面，prepare 写预置规划文件">
        <ComponentBlock c={d.fixture} />
      </Card>

      <Card title="回路（runner）"
            help="agent 怎么跑：单轮对话（say 即收）或假 live（后台推进世界 + 跟随回路）">
        <ComponentBlock c={d.runner} />
      </Card>

      <Card title={"评分器（" + d.graders.length + "）"}
            help="每个轴的期望与判定参数——来自构造参数（类数据）与 describe() 补充的期望">
        {d.graders.map((g, i) => (
          <div key={i} className={i ? "mt-2 border-t border-l1 pt-2" : ""}>
            <div className="flex flex-wrap items-baseline gap-2">
              <span className="font-mono text-label text-strong">{g.name ?? g.class}</span>
              <span className="text-note text-dim">[{g.axis ?? "?"}]</span>
              <span className="text-note text-faint">{g.class} @ {g.module}</span>
            </div>
            <ParamsTable c={g} />
          </div>
        ))}
      </Card>

      <Card title={"该项目的运行记录（" + props.runs.length + "，最新在前）"}>
        {props.runs.length === 0 ? (
          <Empty text="这个项目还没跑过 —— 总览页「跑评测」或 CLI 指定它跑" />
        ) : (
          <table className="w-full text-label">
            <thead>
              <tr className="text-faint">
                <th className="text-left font-normal">时间</th>
                <th className="text-right font-normal">#</th>
                <th className="text-left font-normal">结果</th>
                <th className="text-right font-normal">通过</th>
                <th className="text-left font-normal">失败项</th>
                <th className="text-left font-normal">模型</th>
              </tr>
            </thead>
            <tbody>
              {props.runs.map((r, i) => (
                <tr key={r.run_dir + i}
                    className={"border-t border-l1 " + (props.onOpenRun ? "cursor-pointer hover:bg-inset" : "")}
                    onClick={props.onOpenRun ? () => props.onOpenRun?.(r.run_dir) : undefined}>
                  <td className="py-1 text-dim">{r.ts}</td>
                  <td className="text-right">{r.run_no}</td>
                  <td className={r.outcome === "done" ? "" : "text-[color:var(--warn-fg)]"}>{r.outcome}</td>
                  <td className={"text-right " + (r.failed_graders?.length ? "text-[color:var(--err-fg)]" : "")}>
                    {r.passed == null || r.axes == null ? "—" : `${r.passed}/${r.axes}`}
                  </td>
                  <td className="text-[color:var(--err-fg)]">{r.failed_graders?.join("；")}</td>
                  <td className="text-dim">{r.llm_model}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
    </div>
  );
}

/** 组件块：类名 + 出处 + 构造参数表（嵌套契约组件递归） */
function ComponentBlock({ c }: { c: EvalComponent }) {
  return (
    <div>
      <div className="flex flex-wrap items-baseline gap-2">
        <span className="font-mono text-label text-strong">{c.name ?? c.class}</span>
        <span className="text-note text-faint">{c.class} @ {c.module}</span>
      </div>
      <ParamsTable c={c} />
    </div>
  );
}

function ParamsTable({ c }: { c: EvalComponent }) {
  const keys = Object.keys(c.params);
  if (!keys.length) {
    return (
      <div className="mt-1 text-note text-faint">
        （无构造参数——期望在类逻辑里，见 {c.module}）
      </div>
    );
  }
  return (
    <table className="mt-1 w-full text-label">
      <tbody>
        {keys.map((k) => (
          <tr key={k} className="border-t border-l1">
            <td className="w-44 py-1 align-top text-dim">{k}</td>
            <td className="py-1"><ParamValue v={c.params[k]} /></td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function isComponent(v: unknown): v is EvalComponent {
  return typeof v === "object" && v !== null && "class" in v && "params" in v;
}

/** 参数值渲染：长文本（rubric/期望）折叠展开；嵌套契约组件递归成块 */
function ParamValue({ v }: { v: unknown }) {
  if (typeof v === "string" && v.length > 140) {
    return (
      <details>
        <summary className="cursor-pointer text-note text-faint">
          {v.slice(0, 60)}…（点击展开全文）
        </summary>
        <pre className="mt-1 max-h-72 overflow-y-auto whitespace-pre-wrap rounded bg-inset p-2 text-label text-dim">
          {v}
        </pre>
      </details>
    );
  }
  if (isComponent(v)) {
    return <div className="mt-1 rounded bg-inset p-2"><ComponentBlock c={v} /></div>;
  }
  if (Array.isArray(v)) {
    if (!v.length) return <span className="font-mono text-dim">[]</span>;
    return (
      <div className="space-y-1">
        {v.map((x, i) => (
          <div key={i}><ParamValue v={x} /></div>
        ))}
      </div>
    );
  }
  const text = v === null ? "null（默认）" : typeof v === "string" ? v : JSON.stringify(v);
  return <span className="break-all font-mono text-dim">{text}</span>;
}
