/**
 * 规划客户端（P0）—— 离线生产规划的存储在后端，人与 agent 同一入口（U7）。
 *
 * 一个规划一个 YAML（runtime/plans/）。草稿不落 localStorage —— agent 读不到
 * 浏览器存储，后端文件才是协作载体（用户拍板：不走审批、agent 直接改）。
 * simulate 是后端真 planner 干跑（A3/C7：前端不本地仿真），不需要会话。
 */
import { API_BASE } from "../store/frames";
import type { ProjectionFrame } from "../contract";
import type { AlertItem } from "../panels/AlertsList";

/** 规划前瞻警报：与实时警报同模型（后端 AlertView）；kind 闭集在帧契约外，结构化定义 */
export type PlanSimResult = ProjectionFrame & { alerts: AlertItem[] };

export interface PlanMeta {
  id: string;
  title_zh: string;
  map: string;
  spawn: string;
  locked: boolean;
  items: number;
  updated_at: number;
}

/** 规划队列项（与提案 hunk 的 item 同形：QueueItem 的 JSON 形态） */
export interface PlanQueueItem {
  op: string;
  type: string | null;
  count: number;
  placement: Record<string, unknown> | null;
  task: string | null;
}

export interface Plan extends Omit<PlanMeta, "items"> {
  queue: PlanQueueItem[];
}

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(new URL(path, API_BASE).toString(), init);
  if (!res.ok) {
    const body = (await res.json().catch(() => ({}))) as { detail?: unknown };
    throw new Error(typeof body.detail === "string" ? body.detail : `HTTP ${res.status}`);
  }
  return (await res.json()) as T;
}

const jsonInit = (method: string, body: unknown): RequestInit => ({
  method,
  headers: { "content-type": "application/json" },
  body: JSON.stringify(body),
});

export const listPlans = (): Promise<PlanMeta[]> => call("/api/plans");

export const getPlan = (id: string): Promise<Plan> => call(`/api/plans/${id}`);

/** 新建：`copy_from` 复制既有规划（含默认规划），否则空白 */
export const createPlan = (body: {
  id?: string; title_zh?: string; copy_from?: string; spawn?: string;
}): Promise<Plan> =>
  call("/api/plans", jsonInit("POST", body));

/** 参考模块（I12-B3）：内置战术库 —— 从模板一键落地成规划文件的模板源 */
export interface ModuleMeta {
  id: string;
  title_zh: string;
  items: number;
}

export const listModules = (): Promise<ModuleMeta[]> => call("/api/modules");

/** 从模板新建：模块是唯一真相源，落地成 plans/<id>.yaml（不再手抄队列） */
export const createPlanFromModule = (body: {
  module: string; params?: Record<string, unknown>; id?: string; title_zh?: string;
}): Promise<Plan> =>
  call("/api/plans/from-module", jsonInit("POST", body));

/** 保存整份规划（队列是全量替换，不做 hunk —— 文件是真相源，diff 留给 git） */
export const savePlan = (
  id: string,
  body: { title_zh: string; map: string; spawn: string; queue: PlanQueueItem[] },
): Promise<Plan> =>
  call(`/api/plans/${id}`, jsonInit("PUT", body));

export const removePlan = (id: string): Promise<{ ok: boolean }> =>
  call(`/api/plans/${id}`, { method: "DELETE" });

/** 离线干跑：标准开局种子 + 真 planner 投影；返回投影 payload + 前瞻警报 */
export const simulatePlan = (
  items: PlanQueueItem[],
  opts: { horizon?: number; planId?: string } = {},
): Promise<PlanSimResult> =>
  call("/api/plans/simulate",
       jsonInit("POST", { items, horizon: opts.horizon, plan_id: opts.planId }));
