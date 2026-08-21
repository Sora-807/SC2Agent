/**
 * DOM 侧字号/暗度 token（F11a/F13d）—— G6 的机制落点。
 *
 * 类名由 src/index.css 的 tailwind `@utility` 提供（text-note / text-label / …、
 * text-dim / text-faint / text-ghost）。px 值与 canvas/theme.ts 的 FONT_PX 是同一套
 * token，两边同步由 web/tests/theme.test.ts 锁死。
 * 页面里禁止出现裸 text-[Npx]（theme.test.ts 全量扫描）。
 */

/** 6 个字号 token 的类名（行高已含在 @utility 里，不同字号行高不漂移）。 */
export const T = {
  metricXl: "text-metric-xl",
  metric: "text-metric",
  label: "text-label",
  body: "text-body",
  note: "text-note",
  mono: "text-mono",
} as const;

export type TextToken = keyof typeof T;

/**
 * 颜色 token（F13d）：次级文字的三档暗度 —— 收口一处，别在页面里撒裸 neutral。
 * 语义：dim = 次级信息（能读）、faint = 辅助说明、ghost = 出处/红线注释（不删但不能和数据同权重）。
 */
export const C = {
  dim: "text-dim",
  faint: "text-faint",
  ghost: "text-ghost",
} as const;

/** 字号 + 暗度的常用组合（避免页面里自己拼）。 */
export const TC = {
  noteDim: T.note + " " + C.dim,
  noteFaint: T.note + " " + C.faint,
  noteGhost: T.note + " " + C.ghost,
  labelDim: T.label + " " + C.dim,
  bodyDim: T.body + " " + C.dim,
} as const;
