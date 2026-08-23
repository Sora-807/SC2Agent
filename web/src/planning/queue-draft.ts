/**
 * 生产规划草稿（F9）—— 纯函数，便于单测。
 *
 * 草稿是一条 `DraftItem` 列表（与提案 hunk 的 item 同形），编辑全程在本地；
 * 「提为提案」才碰后端。红线：离线草稿**绝不默认叠加 live**（ADR-0022 反例）。
 */
import type { CatalogStatic, MapStatic } from "../contract";
import type { PlanQueueItem } from "../api/plans";

export interface DraftItem {
  id: string;
  op: "build" | "train" | "research" | "assign_workers";
  type: string | null;
  count: number;
  placement:
    | { kind: "exact"; mark: string }
    | { kind: "in_region"; region: string; index: number | null }
    | null;
  task: "mineral" | "gas" | "idle" | null;
}

let seq = 0;
export const newItemId = (): string => "d" + ++seq + "-" + Date.now().toString(36);

export function emptyItem(): DraftItem {
  return { id: newItemId(), op: "build", type: null, count: 1, placement: null, task: null };
}

/** 规划文件队列项 → 草稿项（把后端规划加载进编辑器；字段缺省如实回退） */
export function draftFromJson(
  rows: readonly {
    op: string; type: string | null; count?: number;
    placement?: unknown; task?: string | null;
  }[] | null,
): DraftItem[] {
  const opOf = (op: string): DraftItem["op"] =>
    op === "train" || op === "research" || op === "assign_workers" ? op : "build";
  return (rows ?? []).map((r) => ({
    id: newItemId(),
    op: opOf(r.op),
    type: r.type ?? null,
    count: Math.max(1, Number(r.count ?? 1)),
    placement: (r.placement as DraftItem["placement"]) ?? null,
    task: (r.task as DraftItem["task"]) ?? null,
  }));
}

/**
 * 复盘/游戏的会话队列 → 规划草稿（2026-08-22 十八轮：复盘生产页复用规划编辑器）。
 * 会话是多队列（main/gas…）而规划是单序列；items[].index 是后端分配的**全局序**，
 * 按 index 摊平即还原规划顺序。`cancel` 是会话运行期的撤销产物，不属于规划，跳过；
 * placement 在会话里已被解析成具体槽位/区域 —— 原样带回去（规划本就吃这个形态）。
 */
export function draftFromSessionQueues(
  queues: readonly {
    items: readonly {
      index: number; op: string; stable_id: string | null; count: number;
      placement?: unknown; task?: string | null;
    }[];
  }[],
): DraftItem[] {
  const rows = queues
    .flatMap((q) => q.items)
    .filter((it) => it.op !== "cancel")
    .sort((a, b) => a.index - b.index);
  return draftFromJson(rows.map((it) => ({
    op: it.op, type: it.stable_id, count: it.count,
    placement: it.placement ?? null, task: it.task ?? null,
  })));
}

/** 成本小计（只来自 catalog；catalog 没有的项标出来，不假装免费） */
export function draftCost(
  items: DraftItem[],
  catalog: CatalogStatic | null,
): { minerals: number; vespene: number; supply: number; missing: string[] } {
  let minerals = 0;
  let vespene = 0;
  let supply = 0;
  const missing: string[] = [];
  for (const it of items) {
    if (it.op === "assign_workers") continue;   // 维持配额不花资源
    if (!it.type) { missing.push("（缺类型）"); continue; }
    const entry = catalog?.entries.find((e) => e.stable_id === it.type);
    if (!entry) { missing.push(it.type); continue; }
    minerals += entry.cost.minerals * it.count;
    vespene += entry.cost.vespene * it.count;
    supply += entry.cost.supply * it.count;
  }
  return { minerals, vespene, supply, missing };
}

/** 草稿 → 提案 hunks（每条一个 insert，按序插入） */
export function draftToHunks(items: DraftItem[]) {
  return items.map((it, i) => ({
    id: "h" + i,
    kind: "insert" as const,
    text_zh: i + ". " + describeItem(it),
    payload: { index: i, item: itemToJson(it) },
  }));
}

export function itemToJson(it: DraftItem): PlanQueueItem {
  return {
    op: it.op,
    type: it.type,
    count: Math.max(1, it.count),
    placement: it.placement,
    task: it.task,
  };
}

export function describeItem(it: DraftItem): string {
  if (it.op === "assign_workers") return "维持 " + (it.task ?? "?") + " " + it.count + " 人";
  const place = it.placement
    ? it.placement.kind === "exact"
      ? " @槽位 " + it.placement.mark
      : " @区域 " + it.placement.region
    : "";
  return it.op + " " + (it.type ?? "（选类型）") + " ×" + it.count + place;
}

/** 地图规划 payload 多带的预留区（REST 层附加，不在 ViewFrame 契约闭集内） */
export type PlacementMap = MapStatic & {
  reserved?: { name?: string | null; label_zh?: string }[] | null;
};

/**
 * 地图上所有可选的放置标记：槽位 + 点位（可选限定「规划id/名」，I8）+
 * 预设固定建造点（全局名，不带前缀）+ 区域。
 * qualifyId 给了就把槽位/点位值写成 `规划id/名` —— 会话装的不是这份规划时
 * 后端会拒（跨规划引用），编辑期就避免写出悬空引用。
 */
export function placementOptions(
  map: PlacementMap | null,
  qualifyId?: string | null,
): { label: string; value: string }[] {
  if (!map) return [];
  const q = (name: string): string => (qualifyId ? qualifyId + "/" + name : name);
  return [
    ...map.build_slots.map((s) => ({ label: "槽位 " + (s.alias_zh || s.name) + "（" + s.name + "）", value: "slot:" + q(s.name) })),
    ...map.pos_marks.map((m) => ({ label: "点位 " + m.name, value: "mark:" + q(m.name) })),
    ...(map.reserved ?? [])
      .filter((r) => r.name)
      .map((r) => ({ label: "预设 " + r.name + "（" + (r.label_zh ?? "") + "）", value: "mark:" + r.name })),
    ...map.regions.leaf.map((r) => ({ label: "区域 " + r.display_name_zh, value: "region:" + r.stable_id })),
  ];
}
