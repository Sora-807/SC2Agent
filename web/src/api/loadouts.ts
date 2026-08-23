/**
 * 装配清单（loadout，B1）：三件套引用（map_plan / strategy / plan[+spawn]）。
 * 只读清单 —— 文件由人管（runtime/loadouts/<id>.yaml），没有写面。
 */
import { API_BASE } from "../store/frames";

export interface LoadoutMeta {
  id: string;
  title_zh: string;
  map_plan: string | null;
  strategy: string | null;
  plan: string | null;
  spawn: string | null;
}

export async function listLoadouts(): Promise<LoadoutMeta[]> {
  const res = await fetch(new URL("/api/loadouts", API_BASE));
  if (!res.ok) throw new Error(`后端返回 ${res.status}`);
  return (await res.json()) as LoadoutMeta[];
}
