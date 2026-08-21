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
  | { kind: "del_mark"; name: string }
  | { kind: "add_slot"; name: string; pos: [number, number]; size: number; slotKind: string }
  | { kind: "del_slot"; name: string };

export type SlotView = {
  name: string;
  /** 锚点 = 世界建造点（格心/格角，ADR-0027：偶数尺寸落在格角） */
  pos: [number, number];
  size: number;
  kind: string;
  /** footprint 左下角格点（由 slotTl 算，与后端 placement.tl_from_pos 同公式） */
  tl: [number, number];
  br: [number, number];
};

/** 格心吸附：世界坐标左下原点，格 (cx,cy) 的中心在 (cx+0.5, cy+0.5) */
export function snapToCellCenter(pos: [number, number]): [number, number] {
  return [Math.floor(pos[0]) + 0.5, Math.floor(pos[1]) + 0.5];
}

/**
 * 槽位锚点 → footprint：TL = ceil(P − size/2)、BR = TL + size − 1。
 * 与后端 tactical_map.placement.BuildSlot.tl_from_pos **同一公式**（偶数尺寸落格角，
 * 奇数尺寸落格心）。前端允许这一份的唯一理由：编辑器的**输入侧**必须算新槽位的
 * footprint 才能画它、才能做重叠校验 —— 渲染已下发数据仍然零换算（红线 C2）。
 * map-draft.test.ts 用黄金用例锁它与后端的公式一致。
 */
export function slotTl(pos: [number, number], size: number): [number, number] {
  return [Math.ceil(pos[0] - size / 2), Math.ceil(pos[1] - size / 2)];
}

/** footprint 闭区间相交判定（含边界，与后端 _overlaps 一致） */
export function slotOverlaps(
  a: { tl: [number, number]; br: [number, number] },
  b: { tl: [number, number]; br: [number, number] },
): boolean {
  return !(a.br[0] < b.tl[0] || b.br[0] < a.tl[0] || a.br[1] < b.tl[1] || b.br[1] < a.tl[1]);
}

/**
 * 草稿按序投影到"合并后的标记表"（含静态 pos_marks）。
 * add 同名 = 覆盖位置（重复放置的意图就是"放这里"）；rename 撞名抛错（UI 先查重）。
 */
export interface DraftProjection {
  marks: MarkView[];
  slots: SlotView[];
}

function mkSlot(name: string, pos: [number, number], size: number, kind: string): SlotView {
  const tl = slotTl(pos, size);
  return { name, pos: [...pos], size, kind, tl, br: [tl[0] + size - 1, tl[1] + size - 1] };
}

export function applyDraft(
  baseMarks: MarkView[],
  baseSlots: SlotView[],
  draft: MapPlanHunk[],
): DraftProjection {
  const byName = new Map<string, MarkView>();
  for (const m of baseMarks) byName.set(m.name, { ...m });
  const slots = new Map<string, SlotView>();
  for (const s of baseSlots) slots.set(s.name, { ...s });

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
      continue;
    }
    if (h.kind === "add_slot") {
      // 同名覆盖位置（与 add_mark 同语义：重复放置的意图就是"放这里"）
      slots.set(h.name, mkSlot(h.name, h.pos, h.size, h.slotKind));
      continue;
    }
    if (h.kind === "del_slot") {
      slots.delete(h.name);
    }
  }
  return { marks: [...byName.values()], slots: [...slots.values()] };
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
/** 草稿 hunk → 提案 hunk（kind 与后端 map_plan 枚举一致，payload 字段名与后端校验对齐） */
export function mapDraftToHunks(
  draft: MapPlanHunk[],
): { id: string; kind: string; text_zh: string; payload: Record<string, unknown> }[] {
  return draft.map((h, i) => {
    const id = "mh" + i;
    switch (h.kind) {
      case "add_mark":
        return { id, kind: "add_mark", text_zh: `新增点位 ${h.name} @ ${h.pos.join(", ")}`,
                 payload: { name: h.name, pos: h.pos } };
      case "move_mark":
        return { id, kind: "move_mark", text_zh: `移动点位 ${h.name} → ${h.pos.join(", ")}`,
                 payload: { name: h.name, pos: h.pos } };
      case "rename_mark":
        return { id, kind: "rename_mark", text_zh: `点位改名 ${h.from} → ${h.to}`,
                 payload: { from: h.from, to: h.to } };
      case "del_mark":
        return { id, kind: "del_mark", text_zh: `删除点位 ${h.name}`, payload: { name: h.name } };
      case "add_slot":
        return { id, kind: "add_slot",
                 text_zh: `新增槽位 ${h.name}（${h.size}×${h.size} ${h.slotKind}）@ ${h.pos.join(", ")}`,
                 payload: { name: h.name, pos: h.pos, size: h.size, kind: h.slotKind } };
      case "del_slot":
        return { id, kind: "del_slot", text_zh: `删除槽位 ${h.name}`, payload: { name: h.name } };
    }
  });
}

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
  if (k === "add_slot") {
    const s = v as { name?: unknown; pos?: unknown; size?: unknown; slotKind?: unknown };
    return typeof s.name === "string"
      && Array.isArray(s.pos) && s.pos.length === 2
      && typeof s.pos[0] === "number" && typeof s.pos[1] === "number"
      && typeof s.size === "number" && typeof s.slotKind === "string";
  }
  if (k === "del_slot") {
    return typeof (v as { name?: unknown }).name === "string";
  }
  return false;
}