/**
 * 源码扫描小工具（供 layout.test.ts / F11 theme.test.ts 等结构约定测试共用）
 *
 * 为什么需要它：结构约定测试要断言"代码里没有 X"，但**注释里往往正好解释了"为什么不用 X"**——
 * 直接 `toContain` 会被自己的注释绊倒（F10 第一次跑就踩了三条）。
 * 所以扫描前先剥注释，让断言只看代码。
 */
import { readFileSync, readdirSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

export const SRC = resolve(dirname(fileURLToPath(import.meta.url)), "..", "src");

/** src 下所有 .ts/.tsx 的绝对路径 */
export function allSources(dir: string = SRC, acc: string[] = []): string[] {
  for (const e of readdirSync(dir, { withFileTypes: true })) {
    const p = resolve(dir, e.name);
    if (e.isDirectory()) allSources(p, acc);
    else if (/\.tsx?$/.test(e.name)) acc.push(p);
  }
  return acc;
}

export const rel = (abs: string): string => abs.slice(SRC.length + 1).replaceAll("\\", "/");

/** 原样读（含注释） */
export const raw = (relPath: string): string => readFileSync(resolve(SRC, relPath), "utf8");

/**
 * 去掉 `//` 与 `/* *\/` 注释后的源码。
 * 认识单/双引号与模板字符串（含转义），所以 `"http://x"` 不会被误当成行注释。
 */
export function stripComments(src: string): string {
  type Mode = "code" | "line" | "block" | "sq" | "dq" | "tpl";
  let out = "";
  let mode: Mode = "code";
  let i = 0;
  while (i < src.length) {
    const c = src[i]!;
    const n = src[i + 1];
    if (mode === "code") {
      if (c === "/" && n === "/") { mode = "line"; i += 2; continue; }
      if (c === "/" && n === "*") { mode = "block"; i += 2; continue; }
      if (c === "'") mode = "sq";
      else if (c === '"') mode = "dq";
      else if (c === "`") mode = "tpl";
      out += c; i += 1; continue;
    }
    if (mode === "line") {
      if (c === "\n") { mode = "code"; out += c; }
      i += 1; continue;
    }
    if (mode === "block") {
      if (c === "*" && n === "/") { mode = "code"; i += 2; } else i += 1;
      continue;
    }
    // 字符串内部
    if (c === "\\") { out += c + (n ?? ""); i += 2; continue; }
    if ((mode === "sq" && c === "'") || (mode === "dq" && c === '"') || (mode === "tpl" && c === "`")) {
      mode = "code";
    }
    out += c; i += 1;
  }
  return out;
}

/** 剥掉注释的源码（结构约定断言一律用它） */
export const code = (relPath: string): string => stripComments(raw(relPath));
