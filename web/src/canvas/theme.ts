/**
 * canvas 主题 token（F11a）—— 字号 / 语义色 / 形状语言 / z 顺序与 alpha 预算。
 *
 * 红线对应：
 * - G6：全站只有 6 个字号 token；DOM 侧的 class 在 shell/tokens.ts（全站唯一允许
 *   出现裸 text-[Npx] 的文件），canvas 侧的 px 字符串在这里。两个文件是同一套 token 的
 *   两种载体，不是两套 token。
 * - G4 / U16：形状语言不跨类复用 —— 建筑=矩形、单位=圆角 chip、标记/槽位=菱形/虚线框、
 *   区域=只填充、命令=细虚线。颜色只表达 owner 与状态，不表达类别。
 */

/** 6 个字号 token（px）。DOM 侧等价物见 shell/tokens.ts。 */
export const FONT_PX = {
  /** 头条数字（资源/供给大数字） */
  metricXl: 22,
  /** 次级数字（指标、计数） */
  metric: 15,
  /** 列表/控件标签 */
  label: 12,
  /** 正文 */
  body: 13,
  /** 脚注、出处与红线注释（不删，但不能和数据同权重） */
  note: 11,
  /** 等宽（tag/seq/坐标） */
  mono: 11,
} as const;

export type FontToken = keyof typeof FONT_PX;

/** canvas 用的 font 字符串（组合进 ctx.font）。 */
export function fontCss(token: FontToken, weight = 400): string {
  const family = token === "mono" ? "ui-monospace, monospace" : "ui-sans-serif, sans-serif";
  return `${weight} ${FONT_PX[token]}px ${family}`;
}

/** 语义色：状态（ok/warn/error/blocked/live）+ owner×4。类别永不进色，只进形状（G4）。 */
const OWNER_COLORS = {
  self: "#34d399",
  ally: "#38bdf8",
  enemy: "#f87171",
  neutral: "#94a3b8",
} as const;

/** owner → 色（未知 owner 降级灰，不猜） */
export function ownerColor(owner: string): string {
  return (OWNER_COLORS as Record<string, string>)[owner] ?? "#888";
}

/** 槽位类别色（类别本该走形状；这是既有约定，保留） */
const SLOT_COLORS = {
  supply: "#a3a3a3",
  production: "#60a5fa",
  addon: "#c084fc",
} as const;

/** 槽位 kind → 色（未知 kind 降级灰） */
export function slotColor(kind: string): string {
  return (SLOT_COLORS as Record<string, string>)[kind] ?? "#666";
}

export const COLOR = {
  ok: "#34d399",
  warn: "#fbbf24",
  error: "#f87171",
  blocked: "#94a3b8",
  live: "#22d3ee",
  /** 画布底色（terrain=null 降级时的纯色底） */
  void: "#111820",
  text: "#e5e7eb",
} as const;

/** 形状语言常量（U16：不跨类复用）。 */
export const SHAPE = {
  /** 建筑：直角矩形（仅描边圆角 0） */
  building: { radius: 0 },
  /** 单位：圆角 chip（聚类显示） / 圆点（个体） */
  unit: { chipRadius: 5, chipPadX: 6, chipPadY: 3 },
  /** 标记/槽位：菱形 / 虚线框 */
  mark: { diamondHalf: 4 },
  slot: { dash: [3, 3], cornerTick: 3 },
  /** 命令连线：细虚线 */
  order: { dash: [3, 3] },
} as const;

/**
 * z 顺序（绘制顺序 = 数组顺序）与 alpha 预算。
 * 饱和色预算留给单位/建筑/标记：地形与静态层一律低 alpha / 去饱和。
 */
export const Z_ORDER = [
  "void", // 纯色底
  "terrain", // 台地位图（含悬崖描边/斜坡）
  "regions", // 区域填充
  "creep",
  "visibility",
  "slots", // 四角刻度（低权）
  "resources", // 矿区细节（默认关）
  "buildings", // 矩形 + 标签
  "units", // chip / 个体
  "clusters", // 敌方聚类（后端字段）
  "orders", // 命令虚线
  "placement", // 摆放调试
  "selection", // 选中光环
] as const;

/** alpha 预算表：每层允许的最大透明度占用（叠加后仍可读的上限）。 */
export const ALPHA_BUDGET = {
  terrain: 1.0, // 烤进位图的色值自带低饱和，不再乘 alpha
  regions: 0.18,
  creep: 0.24,
  visibility: 0.6,
  slotsIdle: 0.35, // 降权后的四角刻度
  slotsActive: 0.8, // 有在途建造指向 / 摆放调试
  buildingFill: 0.16,
  buildingProgress: 1.0,
  orderLine: 0.35,
} as const;

/** LOD 阈值（px/格）：跨过阈值才升一级绘制密度。 */
export const LOD = {
  /** 建筑标签：>= full 画两行文字；>= glyph 只画 1 字色块字形；否则只有色块 */
  buildingLabelFull: 8,
  buildingLabelGlyph: 5,
  /** 单位聚类：< chip 阈值画聚类 chip；>= 阈值画个体 */
  unitChip: 6,
} as const;
