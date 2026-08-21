/**
 * F11a / G6：字号 token 机制化。
 *
 * 规则：全站 6 个字号 token；裸 `text-[Npx]` 只允许出现在 `web/src/shell/tokens.ts`
 * （token 的 DOM 载体）。本测试扫描 web/src 下所有 .tsx，超出 allowlist 的违规当场红。
 *
 * allowlist 是 F11 之前就存在的遗留字号（F13(d)「字号 token 全站化」的清理范围）：
 * F11 只保证**不新增**；F13 清空这个 allowlist 后，任何 tsx 里再出现裸字号都会失败。
 */
import { readFileSync, readdirSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { FONT_PX } from "../src/canvas/theme";
import { T } from "../src/shell/tokens";

const SRC = resolve(dirname(fileURLToPath(import.meta.url)), "..", "src");

/** F13(d) 的清理清单：这些文件里的遗留裸字号在本轮放行（只许减少，不许增加） */
const LEGACY_ALLOWLIST = new Set([
  "charts/ProjectionPairChart.tsx",
  "pages/DebugPage.tsx",
  "pages/FlowPage.tsx",
  "pages/MapPage.tsx",
  "pages/Overview.tsx",
  "pages/PlanningPage.tsx",
  "pages/ProductionPage.tsx",
  "panels/ProposalHost.tsx",
  "panels/ProposalReview.tsx",
  "shell/ChatDock.tsx",
  "shell/IconRail.tsx",
  "shell/StatusChip.tsx",
  "shell/Timeline.tsx",
]);

const BARE_SIZE = /text-\[\d+(?:\.\d+)?px\]/g;

function* tsxFiles(dir: string): Generator<string> {
  for (const name of readdirSync(dir, { withFileTypes: true })) {
    const full = join(dir, name.name);
    if (name.isDirectory()) yield* tsxFiles(full);
    else if (name.name.endsWith(".tsx")) yield full;
  }
}

describe("G6 字号 token", () => {
  it("tsx 里没有裸 text-[Npx]（allowlist 之外；allowlist 是 F13(d) 的清理清单）", () => {
    const violations: string[] = [];
    for (const file of tsxFiles(SRC)) {
      const rel = file.slice(SRC.length + 1).replace(/\\/g, "/");
      if (LEGACY_ALLOWLIST.has(rel)) continue;
      const text = readFileSync(file, "utf8");
      const hits = text.match(BARE_SIZE);
      if (hits) violations.push(`${rel}: ${hits.join(", ")}`);
    }
    expect(violations, `裸字号只允许出现在 shell/tokens.ts：\n${violations.join("\n")}`).toEqual([]);
  });

  it("allowlist 文件确实还有遗留字号（清单过期 = 该从 allowlist 里删掉）", () => {
    const stale: string[] = [];
    for (const rel of LEGACY_ALLOWLIST) {
      const text = readFileSync(join(SRC, rel), "utf8");
      if (!BARE_SIZE.test(text)) stale.push(rel);
      BARE_SIZE.lastIndex = 0;
    }
    expect(stale, `这些文件已干净，应从 allowlist 移除：${stale.join(", ")}`).toEqual([]);
  });

  it("tokens.ts 与 canvas/theme.ts 是同一套 token（DOM 载体与 px 值一一对齐）", () => {
    const keys = Object.keys(FONT_PX) as Array<keyof typeof FONT_PX>;
    expect(keys).toHaveLength(6);
    for (const key of keys) {
      const cls = T[key];
      const m = new RegExp(`text-\\[(\\d+)px\\]`).exec(cls);
      expect(m, `T.${key} 缺 text-[Npx]`).not.toBeNull();
      expect(Number(m![1]), `T.${key} 与 FONT_PX.${key} 不一致`).toBe(FONT_PX[key]);
    }
  });
});
