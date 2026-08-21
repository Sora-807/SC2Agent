/**
 * 地图规划草稿（F14 切片 1）—— 结构化 MapPlanHunk，人与 agent 同一表示。
 *
 * 红线（ADR-0022 反例 / G7）：草稿是**结构化 patch**，不是画布状态 ——
 * 每次编辑追加一条 hunk，applyDraft(base, draft) 纯函数把它投影成"合并后的标记表"。
 * 这样：① 画布渲染与草稿无关（吃投影结果）；② 以后提 map_plan 提案时 hunk 直接进信封；
 * ③ localStorage 存的就是 hunk 序列，可回放、可审。
 *
 * 模型层严格、UI 层守门：rename 撞名在模型层**抛错**（调用方必须先查重）；
 * del/move 指向不存在的名字是**幂等忽略**（草稿按序回放时可能发生）。
 */
import type { MapStatic } from "../contract";

export type MarkView = {
  name: string;
  pos: [number, number];
  description_zh?: string;
};

export type MapPlanHunk =
  | { kind: "add_mark"; name: string; pos: [number, number] }
  | { kind: "move_mark"; name: string; pos: [number, number] }
  | { kind: "rename_mark"; from: string; to: string }
  | { kind: "del_mark"; name: string };

/** 格心吸附：世界坐标左下原点，格 (cx,cy) 的中心在 (cx+0.5, cy+0.5) */
export function snapToCellCenter(pos: [number, number]): [number, number] {
  return [Math.floor(pos[0]) + 0.5, Math.floor(pos[1]) + 0.5];
}

/**
 * 草稿按序投影到"合并后的标记表"（含静态 pos_marks）。
 * add 同名 = 覆盖位置（重复放置的意图就是"放这里"）；rename 撞名抛错（UI 先查重）。
 */
export function applyDraft(base: MarkView[], draft: MapPlanHunk[]): MarkView[] {
  const byName = new Map<string, MarkView>();
  for (const m of base) byName.set(m.name, { ...m });
  for (const h of draft) {
    if (h.kind === "add_mark") {
      byName.set(h.name, { name: h.name, pos: [...h.pos] });
      continue;
    }
    if (h.kind === "move_mark") {
      const m = byName.get(h.name);
      if (m) m.pos = [...h.pos];
      continue;
    }
    if (h.kind === "rename_mark") {
      const m = byName.get(h.from);
      if (!m) continue;
      if (byName.has(h.to) && h.to !== h.from) {
        throw new Error("改名目标已存在：" + h.to + "（UI 必须先查重）");
      }
      byName.delete(h.from);
      m.name = h.to;
      byName.set(h.to, m);
      continue;
    }
    if (h.kind === "del_mark") {
      byName.delete(h.name);
    }
  }
  return [...byName.values()];
}

/** 下一个不冲突的自动名字（mark_1 / mark_2 …） */
export function nextMarkName(taken: Iterable<string>): string {
  const set = new Set(taken);
  for (let i = 1; ; i += 1) {
    const n = "mark_" + i;
    if (!set.has(n)) return n;
  }
}

/** 草稿的本地持久化键（带地图名；地图静态面换了就是另一份草稿） */
export function draftStorageKey(mapName: string): string {
  return "map-plan-draft:" + mapName;
}

/** 读取/写入的边界都包 try/catch：坏 JSON 回空草稿、写不进（隐私模式）静默 */
export function loadDraft(map: MapStatic | null): MapPlanHunk[] {
  if (!map) return [];
  try {
    const raw = localStorage.getItem(draftStorageKey(map.map_name));
    const arr = raw ? (JSON.parse(raw) as unknown[]) : [];
    // 结构校验：只要 hunk 形状不对就整份丢弃（不猜半份）
    return Array.isArray(arr) && arr.every(isHunk) ? (arr as MapPlanHunk[]) : [];
  } catch {
    return [];
  }
}

export function saveDraft(map: MapStatic | null, draft: MapPlanHunk[]): void {
  if (!map) return;
  try {
    localStorage.setItem(draftStorageKey(map.map_name), JSON.stringify(draft));
  } catch {
    /* 存不进就算了 —— 草稿只在内存里生效 */
  }
}

function isHunk(v: unknown): v is MapPlanHunk {
  if (typeof v !== "object" || v === null) return false;
  const k = (v as { kind?: unknown }).kind;
  if (k === "add_mark" || k === "move_mark") {
    const p = (v as { pos?: unknown }).pos;
    return Array.isArray(p) && p.length === 2
      && typeof p[0] === "number" && typeof p[1] === "number"
      && typeof (v as { name?: unknown }).name === "string";
  }
  if (k === "rename_mark") {
    return typeof (v as { from?: unknown }).from === "string"
      && typeof (v as { to?: unknown }).to === "string";
  }
  if (k === "del_mark") {
    return typeof (v as { name?: unknown }).name === "string";
  }
  return false;
}
