/**
 * 策略文件客户端（二十七轮「开放写策略，免审」）—— 策略从此是文件：
 * runtime/strategies/<id>.yaml（strategy + assembly 两段），人与 agent 同一入口。
 * 会话启动时装配（?strategy=<id>），热改不存在 —— 正在跑的会话不受影响。
 */
import { API_BASE } from "../store/frames";

export interface StrategyMeta {
  id: string;
  title_zh: string;
  strategy_id: string;
  locked: boolean;
  updated_at: number;
}

async function get<T>(path: string): Promise<T> {
  const res = await fetch(new URL(path, API_BASE).toString());
  if (!res.ok) {
    const body = (await res.json().catch(() => ({}))) as { detail?: unknown };
    throw new Error(typeof body.detail === "string" ? body.detail : `HTTP ${res.status}`);
  }
  return (await res.json()) as T;
}

export const listStrategies = (): Promise<StrategyMeta[]> => get("/api/strategies");
