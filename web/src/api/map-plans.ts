/**
 * 地图规划客户端（P2 切片 1）—— 默认地图锁定 + 复制新建 + 出生点读取。
 *
 * 一个地图规划一份 YAML（runtime/map-plans/，base_layout 同构）。离线域直改文件
 * （用户拍板不走审批）；保存 = 草稿 hunks 应用到指定出生点分支，校验与 map_plan
 * 提案同一套（重叠/重名 400 带结构化理由）。payload 是 static/map 形状 ——
 * 画布直接渲染，terrain 由前端叠加夹具地形。
 */
import { API_BASE } from "../store/frames";
import type { MapStatic } from "../contract";

export interface MapPlanMeta {
  id: string;
  title_zh: string;
  map_name: string;
  /** 规划属于哪个出生点（规划 = 某地图 × 某方的一种布局，用户拍板的模型） */
  spawn: string;
  locked: boolean;
  slots: number;
  updated_at: number;
}

/** 保存用的 hunk（与 map_plan 提案同构；payload 形状见 map-draft） */
export interface MapPlanSaveHunk {
  id?: string;
  kind: string;
  text_zh?: string;
  payload: Record<string, unknown>;
}

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(new URL(path, API_BASE).toString(), init);
  if (!res.ok) {
    const body = (await res.json().catch(() => ({}))) as { detail?: unknown };
    throw new Error(typeof detailText(body.detail) === "string"
      ? detailText(body.detail) : `HTTP ${res.status}`);
  }
  return (await res.json()) as T;
}

/** 400 的 detail 可能是结构化 {ok, errors[{hunk_id,text_zh}]} —— 拼成可读一句 */
function detailText(detail: unknown): string {
  if (typeof detail === "string") return detail;
  if (detail && typeof detail === "object" && Array.isArray((detail as { errors?: unknown[] }).errors)) {
    const d = detail as { errors: { text_zh?: string }[] };
    return d.errors.map((e) => e.text_zh ?? "?").join("；");
  }
  return "";
}

const jsonInit = (method: string, body: unknown): RequestInit => ({
  method,
  headers: { "content-type": "application/json" },
  body: JSON.stringify(body),
});

export const listMapPlans = (): Promise<MapPlanMeta[]> => call("/api/map-plans");

export const getMapPlanPayload = (id: string): Promise<MapStatic> =>
  call(`/api/map-plans/${id}`);

export const createMapPlan = (body: {
  id?: string; title_zh?: string; copy_from?: string;
}): Promise<MapPlanMeta> => call("/api/map-plans", jsonInit("POST", body));

export const saveMapPlan = (
  id: string, hunks: MapPlanSaveHunk[],
): Promise<{ ok: boolean }> =>
  call(`/api/map-plans/${id}`, jsonInit("PUT", { hunks }));

export const removeMapPlan = (id: string): Promise<{ ok: boolean }> =>
  call(`/api/map-plans/${id}`, { method: "DELETE" });
