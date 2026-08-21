/**
 * F11a/F13d / G6：字号 token 机制化（F13 起全量生效，allowlist 已清空）。
 *
 * 规则：全站 6 个字号 token + 三档暗度 token：
 * - DOM 侧：tailwind @utility（text-note / text-label / …，定义在 src/index.css）；
 * - canvas 侧：canvas/theme.ts 的 FONT_PX；
 * 两边是同一套 token 的两种载体，本测试锁死 px 值同步。
 * 任何 tsx 里出现裸 `text-[Npx]` 都会失败 —— 想加字号先去改 token（并想清楚值不值得）。
 */
import { readFileSync, readdirSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { FONT_PX } from "../src/canvas/theme";
import { T } from "../src/shell/tokens";

const SRC = resolve(dirname(fileURLToPath(import.meta.url)), "..", "src");

const BARE_SIZE = /text-\[\d+(?:\.\d+)?px\]/g;

function* tsxFiles(dir: string): Generator<string> {
  for (const name of readdirSync(dir, { withFileTypes: true })) {
    const full = join(dir, name.name);
    if (name.isDirectory()) yield* tsxFiles(full);
    else if (name.name.endsWith(".tsx")) yield full;
  }
}

describe("G6 字号 token", () => {
  it("tsx 里没有裸 text-[Npx]（全量生效，无 allowlist）", () => {
    const violations: string[] = [];
    for (const file of tsxFiles(SRC)) {
      const rel = file.slice(SRC.length + 1).replace(/\\/g, "/");
      const text = readFileSync(file, "utf8");
      const hits = text.match(BARE_SIZE);
      if (hits) violations.push(`${rel}: ${hits.join(", ")}`);
    }
    expect(violations, `裸字号禁止出现，改用 shell/tokens 的 T token：\n${violations.join("\n")}`).toEqual([]);
  });

  it("index.css 的 @utility 与 canvas/theme.ts 的 FONT_PX 是同一套 token（px 值一一对齐）", () => {
    const css = readFileSync(join(SRC, "index.css"), "utf8");
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
