/** 图层定义与开关（ADR-0023 §2.2 的图层清单，按帧里实际有什么标注可用性） */
import type { MapStatic, WorldFrame } from "../contract";

export type LayerKey =
  | "terrain"
  | "grid"
  | "regions"
  | "slots"
  | "marks"
  | "buildings"
  | "units"
  | "groups"
  | "resources"
  | "orders"
  | "visibility"
  | "creep"
  | "clusters"
  | "placement"
  | "placeable";

export interface LayerDef {
  key: LayerKey;
  label: string;
  /** 默认开 */
  on: boolean;
  /** 这一层需要的数据在不在帧里 —— 不在就置灰并说明（不静默） */
  available(map: MapStatic | null, world: WorldFrame | null): { ok: boolean; why?: string };
}

const always = () => ({ ok: true });

export const LAYERS: LayerDef[] = [
  { key: "terrain", label: "地形", on: true,
    available: (m) => m?.terrain
      ? { ok: true }
      : { ok: false, why: "地形未下发（需真机 B4；sim/离线为纯色底）" } },
  { key: "grid", label: "格点", on: true, available: always,
    // 建筑坐标对齐参考：每 1 格细线、每 5 格粗线；缩放太小自动退成只有粗线（LOD）
  },
  { key: "regions", label: "区域", on: true,
    available: (m) => m?.regions.big_grid
      ? { ok: true }
      : { ok: false, why: "帧里没有区域标签网格" } },
  { key: "slots", label: "建造槽位", on: true,
    available: (m) => (m?.build_slots.length ?? 0) > 0
      ? { ok: true } : { ok: false, why: "该地图模板没有槽位" } },
  // 点位标记（PosMark）：帧里一直有 `map.pos_marks`，但画布**从来没画过它** ——
  // 于是「在线看到我标了什么」这件事根本看不见（地图页只显示计数、规划页只有文本列表）。
  { key: "marks", label: "点位标记", on: true,
    available: (m) => (m?.pos_marks.length ?? 0) > 0
      ? { ok: true } : { ok: false, why: "该地图没有登记点位（PosMark）" } },
  { key: "buildings", label: "建筑", on: true, available: always },
  { key: "units", label: "单位", on: true, available: always },
  { key: "groups", label: "flow 分组", on: true,
    available: (_m, w) => (w?.units.some((u) => u.group_id) ?? false)
      ? { ok: true } : { ok: false, why: "本帧没有单位被 lease 进任何组" } },
  { key: "resources", label: "矿区细节", on: false, available: always },
  // F11e：默认关 + 改名「矿区细节」—— 饱和度改由主基地那行「矿 12/16」表达，
  // 20+ 矽点不再压过信息（根因 J）；要看细节时手动开。
  { key: "orders", label: "命令连线", on: false, available: always },
  { key: "visibility", label: "视野", on: false,
    available: (_m, w) => w?.grids?.visibility
      ? { ok: true } : { ok: false, why: "帧里没下发视野层（仅变化时发）" } },
  { key: "creep", label: "菌毯", on: false,
    available: (_m, w) => w?.grids?.creep
      ? { ok: true } : { ok: false, why: "帧里没下发菌毯层" } },
  { key: "clusters", label: "敌方聚类", on: true,
    available: (_m, w) => w?.enemy_clusters
      ? { ok: true } : { ok: false, why: "后端聚类算法未实现（词表里登记为 forbidden）" } },
  { key: "placement", label: "摆放调试", on: false, available: always },
  // F16：可建区（placeable=1 淡绿 tint）—— 编辑背景，回答「哪里能放」；规划页强制开
  { key: "placeable", label: "可建区", on: false,
    available: (m) => m?.terrain?.placeable
      ? { ok: true }
      : { ok: false, why: "placeable 未下发（需地形；离线夹具已含合成地形）" } },
];

export type LayerState = Record<LayerKey, boolean>;

export const defaultLayers = (): LayerState =>
  Object.fromEntries(LAYERS.map((l) => [l.key, l.on])) as LayerState;
