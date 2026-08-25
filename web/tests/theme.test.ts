/**
 * F11a/F13d / G6：字号 token 机制化（F13 起全量生效，allowlist 已清空）。
 *
 * 规则：全站 6 个字号 token + 三档暗度 token：
 * - DOM 侧：tailwind @utility（text-note / text-label / …，定义在 src/index.css）；
 * - canvas 侧：canvas/theme.ts 的 FONT_PX；
 * 两边是同一套 token 的两种载体，本测试锁死 px 值同步。
 * 任何 tsx 里出现裸 `text-[Npx]` 都会失败 —— 想加字号先去改 token（并想清楚值不值得）。
 */
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { FONT_PX } from "../src/canvas/theme";
import { T } from "../src/shell/tokens";
import { SRC, allSources, rawAbs, rel as relPath } from "./source-scan";

// 约定扫描（custom-lint）：全仓禁令无行为可迁（N4/D3 分类），读取走 source-scan 单点。
const tsxFiles = (): string[] => allSources().filter((p) => p.endsWith(".tsx"));

const BARE_SIZE = /text-\[\d+(?:\.\d+)?px\]/g;

describe("G6 字号 token", () => {
  it("tsx 里没有裸 text-[Npx]（全量生效，无 allowlist）", () => {
    const violations: string[] = [];
    for (const file of tsxFiles()) {
      const rel = relPath(file);
      const text = rawAbs(file);
      const hits = text.match(BARE_SIZE);
      if (hits) violations.push(`${rel}: ${hits.join(", ")}`);
    }
    expect(violations, `裸字号禁止出现，改用 shell/tokens 的 T token：\n${violations.join("\n")}`).toEqual([]);
  });

  it("字号全走 token：text-xs / text-sm 也禁（按钮级打磨轮收编，2026-08-22）", () => {
    const violations: string[] = [];
    for (const file of tsxFiles()) {
      const rel = relPath(file);
      const text = rawAbs(file);
      const hits = text.match(/text-(?:xs|sm)\b/g);
      if (hits) violations.push(`${rel}: ${[...new Set(hits)].join(", ")}`);
    }
    expect(violations, `text-xs→text-label、text-sm→text-body：\n${violations.join("\n")}`).toEqual([]);
  });

  it("index.css 的 @utility 与 canvas/theme.ts 的 FONT_PX 是同一套 token（px 值一一对齐）", () => {
    const css = rawAbs(join(SRC, "index.css"));
    const keys = Object.keys(FONT_PX) as Array<keyof typeof FONT_PX>;
    expect(keys).toHaveLength(6);
    for (const key of keys) {
      // FONT_PX 的 camelCase 键 → CSS 工具类的 kebab-case 名（metricXl → metric-xl）
      const kebab = key.replace(/[A-Z]/g, (m) => "-" + m.toLowerCase());
      const re = new RegExp(`@utility\\s+text-${kebab}\\s*\\{[^}]*font-size:\\s*(\\d+)px`);
      const m = re.exec(css);
      expect(m, `index.css 缺 @utility text-${kebab}`).not.toBeNull();
      expect(Number(m![1]), `text-${kebab} 与 FONT_PX.${key} 不一致`).toBe(FONT_PX[key]);
      // tokens.ts 的 T 指到同一个工具类
      expect(T[key], `T.${key} 应该是 text-${kebab}`).toBe(`text-${kebab}`);
    }
  });
});

/* ── 双主题（2026-08-22 批次）：字面色禁止回归 + 调色板完整性 ── */

describe("双主题 token", () => {
  it("tsx 里没有裸 bg/text/border-neutral-N（一律走语义 token，配色才可维护）", () => {
    const violations: string[] = [];
    for (const file of tsxFiles()) {
      const rel = relPath(file);
      const text = rawAbs(file);
      const hits = text.match(/(?:^|[\s"'])(?:bg|text|border|ring)-neutral-\d/g);
      if (hits) violations.push(`${rel}: ${[...new Set(hits)].join(", ")}`);
    }
    expect(violations, `中性色改用 bg-panel/raised/inset、border-l1/l2、text-strong/dim：\n${violations.join("\n")}`).toEqual([]);
  });

  it("index.css 调色板定义全变量 + 语义 @utility 齐（2026-08-22 十五轮：蓝底白卡定稿）", () => {
    const css = rawAbs(join(SRC, "index.css"));
    const vars = ["--bg-base", "--bg-panel", "--bg-raised", "--bg-inset", "--bg-active", "--bg-select",
      "--on-base-text",
      "--border-l1", "--border-l2", "--border-on-base",
      "--text-strong", "--text-dim", "--text-faint",
      "--text-ghost", "--uplot-grid", "--uplot-cursor",
      "--accent-blue", "--accent-blue-soft", "--accent-blue-fg",
      "--accent-pink", "--accent-pink-soft", "--accent-pink-fg",
      "--accent-yellow", "--accent-yellow-soft", "--accent-yellow-fg"];
    const head = css.indexOf(":root");
    expect(head).toBeGreaterThan(0);
    const body = css.slice(head, css.indexOf("}", head));
    for (const v of vars) expect(body, `:root 缺 ${v}`).toContain(v);
    for (const u of ["bg-base", "bg-panel", "bg-raised", "bg-inset", "bg-active",
      "border-l1", "border-l2", "border-chrome", "bg-select", "text-strong",
      "bg-blue-soft", "bg-pink-soft", "bg-yellow-soft",
      "text-blue-fg", "text-pink-fg", "text-yellow-fg"]) {
      expect(css, `缺 @utility ${u}`).toContain(`@utility ${u} `);
    }
  });

  it("蓝底白卡锚点值锁死：外围 #9cbce3 / 卡片 #ffffff / 粉 #e1dbe9 / 黄 #f5c386（用户给的色板，不许漂）", () => {
    const css = rawAbs(join(SRC, "index.css"));
    expect(css).toMatch(/--bg-base:\s*#9cbce3/);
    expect(css).toMatch(/--bg-panel:\s*#ffffff/);
    expect(css).toMatch(/--bg-inset:\s*#e1dbe9/);
    expect(css).toMatch(/--accent-yellow:\s*#f5c386/);
  });

  it("暗色残留禁令：tsx 里不许再出现 x-950/x-900 洗底与 300 档浅字（白底上看不见）", () => {
    const violations: string[] = [];
    for (const file of tsxFiles()) {
      const rel = relPath(file);
      const text = rawAbs(file);
      const hits = text.match(/(?:bg|text|border)-(?:red|sky|emerald|amber|fuchsia)-\d+\b/g);
      if (hits) violations.push(`${rel}: ${[...new Set(hits)].join(", ")}`);
    }
    expect(violations, `状态色一律走 ok/warn/err/accent token（violet-500 思考色除外，在 ChatDock 白名单）：\n${violations.join("\n")}`).toEqual([]);
  });

  it("单主题：不再有 light/sakura 调色板（用户拍板先只做一套微调）", () => {
    const css = rawAbs(join(SRC, "index.css"));
    expect(css).not.toContain('html[data-theme="light"]');
    expect(css).not.toContain('html[data-theme="sakura"]');
  });

  it("CSS 注释里不许出现 */ 序列以外的注释自杀写法（2026-08-22 事故锁）", () => {
    // 事故：注释里写了「--bg-*/--text-*」—— */ 提前闭合注释，@utility 整段被判非法，
    // 产物丢掉全部语义类 = 「全站越来越白、边框消失」。同类写法永远禁止。
    const css = rawAbs(join(SRC, "index.css"));
    for (const m of css.matchAll(/\/\*[\s\S]*?\*\//g)) {
      const body = m[0].slice(2, -2);
      expect(body.includes("*/"), "注释体内出现 */（会提前闭合）：" + m[0].slice(0, 60)).toBe(false);
    }
  });
});
