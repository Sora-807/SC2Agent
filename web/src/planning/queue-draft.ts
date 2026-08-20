/**
 * 生产规划草稿（F9）—— 纯函数，便于单测。
 *
 * 草稿是一条 `DraftItem` 列表（与提案 hunk 的 item 同形），编辑全程在本地；
 * 「提为提案」才碰后端。红线：离线草稿**绝不默认叠加 live**（ADR-0022 反例）。
 */
import type { CatalogStatic, MapStatic } from "../contract";

export interface DraftItem {
  id: string;
  op: "build" | "train" | "assign_workers";
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

export function itemToJson(it: DraftItem): Record<string, unknown> {
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

/** 地图上所有可选的放置标记（槽位 + 点位 + 区域） */
export function placementOptions(map: MapStatic | null): { label: string; value: string }[] {
  if (!map) return [];
  return [
    ...map.build_slots.map((s) => ({ label: "槽位 " + s.name + "（" + s.kind + "）", value: "slot:" + s.name })),
    ...map.pos_marks.map((m) => ({ label: "点位 " + m.name, value: "mark:" + m.name })),
    ...map.regions.leaf.map((r) => ({ label: "区域 " + r.display_name_zh, value: "region:" + r.stable_id })),
  ];
}
