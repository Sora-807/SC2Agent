/**
 * 源码扫描小工具（约定扫描 / 决策锁测试的**单点**，N4 治理定稿）
 *
 * 为什么需要它：结构约定测试要断言"代码里没有 X"，但**注释里往往正好解释了"为什么不用 X"**——
 * 直接 `toContain` 会被自己的注释绊倒（F10 第一次跑就踩了三条）。
 * 所以扫描前先剥注释，让断言只看代码。
 *
 * 政策（N4 / D3，2026-08-25）：tests/ 里的 readFileSync 只允许三类正当用途——
 * ① fixtures 加载（economy/source/schema/graph/gantt 等）；
 * ② 约定扫描（custom-lint：theme/layout 的全仓禁令——无行为可迁，走本助手单点）；
 * ③ 跨语言契约对账（contract 精确解析后端 Python 的 REV/TOPICS）。
 * 组件接线/决策锁（chat-dock/map-canvas/charts 的交互段）也统一经本助手读源——
 * 它们锁的是**用户拍板的 UI/交互决策**（迁渲染测试不改变锁的性质，且本仓无渲染
 * 基建、UI 由用户自测）；重构允许同步改断言字符串，但不许静默删测试。
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

/** 绝对路径版（allSources() 的产物直接喂）：剥注释 */
export const codeAbs = (abs: string): string => stripComments(readFileSync(abs, "utf8"));

/** 绝对路径版：原样读（含注释） */
export const rawAbs = (abs: string): string => readFileSync(abs, "utf8");
