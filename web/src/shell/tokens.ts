/**
 * DOM 侧字号 token（F11a）—— G6 的机制落点。
 *
 * **全站唯一允许出现 `text-[Npx]` 的文件**（theme.test.ts 扫描 tsx 断言无裸字号；
 * 遗留 allowlist 见 web/tests/theme.test.ts，F13(d) 字号全站化时清空）。
 * px 值与 canvas 侧 canvas/theme.ts 的 FONT_PX 是同一套 token —— 改一边必须同改另一边。
 */

/** 每个字号 token 的 tailwind class（含行高，避免不同字号行高漂移）。 */
export const T = {
  metricXl: "text-[22px] leading-7",
  metric: "text-[15px] leading-5",
  label: "text-[12px] leading-4",
  body: "text-[13px] leading-5",
  note: "text-[11px] leading-4",
  mono: "text-[11px] leading-4 font-mono",
} as const;

export type TextToken = keyof typeof T;
