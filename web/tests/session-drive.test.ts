/**
 * 实时驾驶整改（2026-08-21）—— 源码扫描式回归锁，沿用 layout.test.ts 的惯例。
 *
 * 本轮锁的三件事都是**纪律约定**（不是渲染输出）：
 * 1. 会话操作错误不吞：sessionAction 失败必须带后端 detail 返回 —— 曾经 400 被吞成
 *    null，UI 表现成「点启动真机没反应」，根因链条的起点；
 * 2. 顶栏没有沙盒入口：假世界数据曾把「SC2 没启动」伪装成「连上了但地图空 +
 *    来历不明的槽位」（sim 驱动保留在后端，测试/agent 走 REST）；
 * 3. 不用 window.confirm 做关键确认：嵌入式浏览器可能直接拦掉（= 又一个静默无效），
 *    真机启动用两段式按钮确认。
 *
 * 断言一律走 code()（剥掉注释）—— 注释里正好会解释"为什么不用"。
 */
import { describe, expect, it } from "vitest";
import { code } from "./source-scan";

describe("会话操作错误不吞（commands.ts）", () => {
  const src = code("api/commands.ts");

  it("sessionAction 返回类型有失败分支（detail 给 UI 显形）", () => {
    expect(src).toContain("| { ok: false; detail: string }");
  });

  it("sessionAction 函数体不再有静默吞错（fetchSessionInfo 的 null 是探测语义，不在此列）", () => {
    const fn = src.slice(
      src.indexOf("export async function sessionAction"),
      src.indexOf("export async function fetchSessionInfo"),
    );
    expect(fn).not.toContain("return null");
  });
});

describe("顶栏会话区纪律（SessionBar.tsx）", () => {
  const bar = code("shell/SessionBar.tsx");

  it("没有「启动沙盒」按钮（假世界入口退役）", () => {
    expect(bar).not.toContain("启动沙盒");
    expect(bar).not.toContain('startSession("sim")');
  });

  it("不用 window.confirm（嵌入式浏览器会拦 = 静默无效）", () => {
    expect(bar).not.toContain("window.confirm");
  });

  it("真机有关闭入口：按驱动改名而不是泛泛的「停止会话」", () => {
    expect(bar).toContain("关闭真机");
    expect(bar).not.toContain("停止会话");
  });

  it("状态/地图/游戏时间三个 Pill 已摘（信息在会话 label 与时间线上）", () => {
    expect(bar).not.toContain('<Pill label="状态"');
    expect(bar).not.toContain('<Pill label="地图"');
    expect(bar).not.toContain('<Pill label="游戏时间"');
  });

  it("播放/暂停只属于复盘（回放语义），驾驶态只有「回到实时」", () => {
    expect(bar).toMatch(/mode === "replay" && !caps\.live/);
  });

  it("规划 id 发送前过 pickMapPlan 兜底（不吃「不存在」的 400）", () => {
    expect(bar).toContain("pickMapPlan(mapPlans, mapPlanId)");
  });
});
