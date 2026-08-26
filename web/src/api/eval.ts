/**
 * 评测客户端（2026-08-25 前端面 + PLAN-EVAL-FRONTEND 批 A 钻取）——
 * overview / 项目详情。
 *
 * 数据源三份：REGISTRY（场景，注册即现）、index.jsonl（运行记录）、
 * result.json/grades.json（run 全量指标）。全部只读。
 */
import { API_BASE } from "../store/frames";

export interface EvalScenario {
  id: string;
  tags: string[];
  runs: number | null;
  judge_model: string | null;
  graders: string[];
}

export interface EvalRun {
  ts: string;
  project: string;
  run_no: number;
  outcome: string;
  llm_model: string | null;
  prompt_hash: string | null;
  passed: number | null;
  axes: number | null;
  failed_graders: string[];
  label: string;
  run_dir: string;
  report: string | null;
}

export interface EvalJob {
  state: "running" | "done" | "error";
  started_at: number;
  label: string;
  runs: number;
  log: string[];
  error: string | null;
  report: string | null;
}

export interface Overview {
  scenarios: EvalScenario[];
  runs: EvalRun[];
  run_howto: string;
  eval_root: string;
  job: EvalJob | null;
}

/** 一个契约组件（fixture/runner/grader）的 introspection 描述（eval/describe.py） */
export interface EvalComponent {
  class: string;
  module: string;
  name?: string;
  axis?: string;
  params: Record<string, unknown>;
}

export interface ProjectDetail {
  id: string;
  tags: string[];
  runs: number | null;
  judge_model: string | null;
  task: { text: string; note: string; max_turns: number | null };
  fixture: EvalComponent;
  runner: EvalComponent;
  graders: EvalComponent[];
}

async function call<T>(path: string): Promise<T> {
  const res = await fetch(new URL(path, API_BASE).toString());
  if (!res.ok) {
    const body = (await res.json().catch(() => ({}))) as { detail?: unknown };
    throw new Error(typeof body.detail === "string" ? body.detail : `HTTP ${res.status}`);
  }
  return (await res.json()) as T;
}

export const fetchOverview = (): Promise<Overview> => call("/api/eval/overview");

export const fetchProject = (id: string): Promise<ProjectDetail> =>
  call(`/api/eval/projects/${encodeURIComponent(id)}`);
