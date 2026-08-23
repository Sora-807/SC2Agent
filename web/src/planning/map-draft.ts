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
  | { kind: "add_slot"; name: string; pos: [number, number]; size: number; slotKind: string;
      aliasZh?: string }
  | { kind: "del_slot"; name: string };

export type SlotView = {
  name: string;
  /** 中文别名（展示用；标记归 name —— 与后端约定一致，rev 14） */
  aliasZh?: string;
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

/** F16：放置预览（吸附 + 合法性判定，纯函数可测）。
 * 规划页 mousemove 时算好传给画布当 ghost；点击时用同一个结果落笔 ——
 * 预览与落笔永远一致（不会预览绿、点了却拒）。 */
export interface PlacementPreview {
  /** 吸附后的锚点（格心） */
  pos: [number, number];
  tl: [number, number];
  br: [number, number];
  /** 地形可建（footprint 每格 placeable=1）；地形未下发时 true（未知不算非法，如实降级） */
  placeable: boolean;
  /** 与哪个既有槽位重叠（null = 不重叠） */
  overlaps: string | null;
  /** 压住了哪类固定建造点（base/geyser/mineral；null = 没压） */
  reservedKind: string | null;
  /** 总体合法（点位不受地形/重叠约束，恒 true） */
  ok: boolean;
  /** 非法原因（ok=false 时非空；zh 文案由页侧拼，这里只给机器码） */
  reason: "overlap" | "reserved" | "unplaceable" | null;
}

export function previewPlacement(opts: {
  mode: "mark" | "slot";
  /** 未吸附的世界坐标 */
  pos: [number, number];
  /** slot 的 footprint 尺寸 */
  size?: number;
  /** 当前投影里的全部槽位（重叠校验用） */
  slots: readonly { name: string; tl: [number, number]; br: [number, number] }[];
  /** placeable 查询（x/y 为格点坐标）；null = 地形未下发 */
  placeableAt?: ((x: number, y: number) => boolean) | null;
  /** 固定建造点预留区（基地/气井/矿脉脚印，后端矩形）：slot 不可占用 */
  reserved?: readonly { tl: [number, number]; br: [number, number]; kind: string }[] | null;
}): PlacementPreview {
  const pos = snapToCellCenter(opts.pos);
  if (opts.mode === "mark") {
    return { pos, tl: pos, br: pos, placeable: true, overlaps: null, reservedKind: null, ok: true, reason: null };
  }
  const size = opts.size ?? 2;
  const tl = slotTl(pos, size);
  const br: [number, number] = [tl[0] + size - 1, tl[1] + size - 1];
  let overlaps: string | null = null;
  for (const s of opts.slots) {
    if (slotOverlaps({ tl, br }, { tl: s.tl, br: s.br })) {
      overlaps = s.name;
      break;
    }
  }
  // 固定建造点（基地/气井/矿脉）：placeable 栅格在这些位置是 1（SC2 的栅格不含
  // 资源占用），必须显式挡 —— 理由单独给 "reserved"。
  let reservedKind: string | null = null;
  if (overlaps === null) {
    for (const r of opts.reserved ?? []) {
      if (slotOverlaps({ tl, br }, { tl: r.tl, br: r.br })) {
        reservedKind = r.kind;
        break;
      }
    }
  }
  let placeable = true;
  if (opts.placeableAt && reservedKind === null) {
    placeable = true;
    for (let y = tl[1]; y <= br[1]; y += 1) {
      for (let x = tl[0]; x <= br[0]; x += 1) {
        if (!opts.placeableAt(x, y)) {
          placeable = false;
          break;
        }
      }
      if (!placeable) break;
    }
  }
  const reason = overlaps !== null ? "overlap"
    : reservedKind !== null ? "reserved"
      : placeable ? null : "unplaceable";
  return { pos, tl, br, placeable, overlaps, ok: reason === null, reason, reservedKind };
}

/**
 * 草稿按序投影到"合并后的标记表"（含静态 pos_marks）。
 * add 同名 = 覆盖位置（重复放置的意图就是"放这里"）；rename 撞名抛错（UI 先查重）。
 */
export interface DraftProjection {
  marks: MarkView[];
  slots: SlotView[];
}

function mkSlot(name: string, pos: [number, number], size: number, kind: string,
                aliasZh?: string): SlotView {
  const tl = slotTl(pos, size);
  return { name, aliasZh, pos: [...pos], size, kind, tl, br: [tl[0] + size - 1, tl[1] + size - 1] };
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
      slots.set(h.name, mkSlot(h.name, h.pos, h.size, h.slotKind, h.aliasZh));
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

/** 槽位类别 → 简写字母与中文（自动命名用；约定见后端 SLOT_NAME_RE，rev 14） */
const SLOT_LETTER: Record<string, { letter: string; zh: string }> = {
  supply: { letter: "D", zh: "补给站" },
  production: { letter: "R", zh: "兵营" },
  addon: { letter: "R", zh: "兵营" },   // 挂件位：字母跟母建筑，UI 未知母建筑 → R 系兜底
};

/** 按约定生成下一个槽位名 + 自动中文别名（supply→D17/补给站17；addon→R5+/兵营5挂件位）。 */
export function nextSlotName(slotKind: string, taken: Iterable<string>): { name: string; aliasZh: string } {
  const meta = SLOT_LETTER[slotKind] ?? { letter: "R", zh: "建筑" };
  const set = new Set(taken);
  const addon = slotKind === "addon";
  for (let i = 1; i <= 99; i += 1) {
    const name = `${meta.letter}${i}${addon ? "+" : ""}`;
    if (!set.has(name)) {
      return { name, aliasZh: addon ? `${meta.zh}${i}挂件位` : `${meta.zh}${i}` };
    }
  }
  throw new Error("槽位序号用尽（1-99）");
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
                 payload: { name: h.name, pos: h.pos, size: h.size, kind: h.slotKind,
                            alias_zh: h.aliasZh ?? "" } };
      case "del_slot":
        return { id, kind: "del_slot", text_zh: `删除槽位 ${h.name}`, payload: { name: h.name } };
    }
  });
}

/** MapStatic.build_slots → 草稿投影的基座（build_point/tl/br 都是后端给的，零换算 C2） */
export function baseSlotsOf(slots: readonly {
  name: string; build_point: [number, number];
  size: number; kind: string; tl: [number, number]; br: [number, number];
}[]): SlotView[] {
  return slots.map((s) => ({
    name: s.name, pos: s.build_point, size: s.size, kind: s.kind, tl: s.tl, br: s.br,
  }));
}

/** 提案 hunk（后端枚举 + payload 字段名）→ 本地草稿 hunk（审批叠加画布用，反向映射）。
 *  add_slot 的 payload.kind 是**槽位类别**，转成草稿的 slotKind（撞名坑，见 add_slot 定义）。 */
export function hunksToDraft(
  hunks: readonly { kind: string; payload: Record<string, unknown> }[],
): MapPlanHunk[] {
  const out: MapPlanHunk[] = [];
  for (const h of hunks) {
    const p = h.payload;
    switch (h.kind) {
      case "add_mark":
        out.push({ kind: "add_mark", name: String(p["name"]),
                   pos: p["pos"] as [number, number] });
        break;
      case "move_mark":
        out.push({ kind: "move_mark", name: String(p["name"]),
                   pos: p["pos"] as [number, number] });
        break;
      case "rename_mark":
        out.push({ kind: "rename_mark", from: String(p["from"]), to: String(p["to"]) });
        break;
      case "del_mark":
        out.push({ kind: "del_mark", name: String(p["name"]) });
        break;
      case "add_slot":
        out.push({ kind: "add_slot", name: String(p["name"]),
                   pos: p["pos"] as [number, number],
                   size: Number(p["size"]), slotKind: String(p["kind"]) });
        break;
      case "del_slot":
        out.push({ kind: "del_slot", name: String(p["name"]) });
        break;
      default:
        break;   // 其它类型的 hunk 不属于地图草稿（部分接受后的残留也安全跳过）
    }
  }
  return out;
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