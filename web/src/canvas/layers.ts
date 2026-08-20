/** 图层定义与开关（ADR-0023 §2.2 的图层清单，按帧里实际有什么标注可用性） */
import type { MapStatic, WorldFrame } from "../contract";

export type LayerKey =
  | "terrain"
  | "regions"
  | "slots"
  | "buildings"
  | "units"
  | "groups"
  | "resources"
  | "orders"
  | "visibility"
  | "creep"
  | "clusters"
  | "placement";

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
  { key: "regions", label: "区域", on: true,
    available: (m) => m?.regions.big_grid
      ? { ok: true }
      : { ok: false, why: "帧里没有区域标签网格" } },
  { key: "slots", label: "建造槽位", on: true,
    available: (m) => (m?.build_slots.length ?? 0) > 0
      ? { ok: true } : { ok: false, why: "该地图模板没有槽位" } },
  { key: "buildings", label: "建筑", on: true, available: always },
  { key: "units", label: "单位", on: true, available: always },
  { key: "groups", label: "flow 分组", on: true,
    available: (_m, w) => (w?.units.some((u) => u.group_id) ?? false)
      ? { ok: true } : { ok: false, why: "本帧没有单位被 lease 进任何组" } },
  { key: "resources", label: "矿区饱和度", on: true, available: always },
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
];

export type LayerState = Record<LayerKey, boolean>;

export const defaultLayers = (): LayerState =>
  Object.fromEntries(LAYERS.map((l) => [l.key, l.on])) as LayerState;
