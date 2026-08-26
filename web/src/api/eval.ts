/**
 * 评测客户端（2026-08-25 前端面 + PLAN-EVAL-FRONTEND 批 A 钻取）——
 * overview / 项目详情 / （批 B：run 详情、提示词全文）。
 *
 * 数据源三份：REGISTRY（场景，注册即现）、index.jsonl（运行记录）、
 * result.json/grades.json（run 全量指标）。全部只读。
 */
import { API_BASE } from "../store/frames";
import type { ChatChange } from "./agent-chat";

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

/** run 详情（GET /api/eval/runs/{run_dir}）：result.json 直读 + grades + index 行 */
export interface RunDetail {
  meta: {
    run_no?: number;
    llm_model?: string;
    outcome?: string;
    duration_s?: number;
    prompt_hash?: string;
    seed_hash?: string;
    input_tokens?: number;
    output_tokens?: number;
    reasoning_clipped?: number;
  };
  tool_calls: {
    tool: string | null;
    args?: unknown;
    turn_no?: number;
    duration_ms?: number;
    result_preview?: string | null;
  }[];
  final_text: string;
  reasoning: string[];
  segments: unknown[];
  proposals: {
    id: string;
    title_zh: string | null;
    rationale_zh: string | null;
    hunks: { id: string; kind: string; text_zh: string | null;
             payload?: { item?: { op?: string; type?: string } } }[];
    validation: { ok: boolean; problems?: string[] } | null;
  }[];
  changes: ChatChange[];
  workspace: Record<string, number>;
  session: { state?: string; game_time?: number; alive?: boolean } | null;
  messages_count: number;
  messages: { role: string | null; content: string }[];
  grades: { axis: string; grader: string; passed: boolean | null;
            score: number | null; reason_zh: string }[];
  /** 匹配的 index 行；中断批次的孤儿 run 为 null（前端标「未入账」） */
  index_row: EvalRun | null;
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

/** run_dir 是相对 eval_root 的路径（含 `/` 与 `+`）——path 形态是字面量 */
export const fetchRun = (runDir: string, withMessages = false): Promise<RunDetail> =>
  call(`/api/eval/runs/${runDir}${withMessages ? "?messages=1" : ""}`);

/** 提示词全文快照（text/plain；中断批次没有 → 抛错带后端说明） */
export async function fetchRunPrompt(runDir: string): Promise<string> {
  const res = await fetch(new URL(`/api/eval/runs/${runDir}/prompt`, API_BASE).toString());
  if (!res.ok) {
    const body = (await res.json().catch(() => ({}))) as { detail?: unknown };
    throw new Error(typeof body.detail === "string" ? body.detail : `HTTP ${res.status}`);
  }
  return res.text();
}
