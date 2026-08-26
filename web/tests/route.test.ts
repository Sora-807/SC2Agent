/**
 * hash 路由的查询参数解析（2026-08-22 改动 chip 深链）：
 * `#/plan-production?plan=agent-m1` 要能同时给出页与选中对象。
 */
import { describe, expect, it } from "vitest";
import { parseRoute } from "../src/shell/route";

describe("parseRoute：页 + 查询参数", () => {
  it("纯页（无参数）不受影响", () => {
    expect(parseRoute("overview").page).toBe("overview");
    expect(parseRoute("").page).toBe("overview");
    expect([...parseRoute("overview").params.keys()]).toHaveLength(0);
  });

  it("带查询参数：页与参数都解析出来", () => {
    const r = parseRoute("plan-production?plan=agent-m1");
    expect(r.page).toBe("plan-production");
    expect(r.params.get("plan")).toBe("agent-m1");
  });

  it("地图规划深链（?map=）与多参数", () => {
    const r = parseRoute("plan-map?map=agent-m2&x=1");
    expect(r.page).toBe("plan-map");
    expect(r.params.get("map")).toBe("agent-m2");
    expect(r.params.get("x")).toBe("1");
  });

  it("旧链接 #/planning 兼容落生产规划", () => {
    expect(parseRoute("planning").page).toBe("plan-production");
    expect(parseRoute("planning?plan=x").params.get("plan")).toBe("x");
  });

  it("未知页落 overview（参数不丢）", () => {
    const r = parseRoute("nonsense?plan=x");
    expect(r.page).toBe("overview");
    expect(r.params.get("plan")).toBe("x");
  });
});

describe("评测页接线（2026-08-25 前端面 + PLAN-EVAL-FRONTEND 批 A 钻取）", () => {
  it("diag 组含 eval 页且 App 挂载 EvalPage（源码级锁）", async () => {
    const { code } = await import("./source-scan");
    const route = code("shell/route.ts");
    expect(route).toContain('{ key: "eval", label: "评测"');
    const app = code("App.tsx");
    expect(app).toContain('page === "eval" && <EvalPage projectId={params.get("project")} />');
    expect(app).toContain('from "./pages/EvalPage"');
    const api = code("api/eval.ts");
    expect(api).toContain("/api/eval/overview");
    expect(api).toContain("/api/eval/projects/");
  });

  it("钻取 hash 必须走 URLSearchParams（run_dir 含 +，字符串拼接会解码成空格）", async () => {
    const { code } = await import("./source-scan");
    const page = code("pages/EvalPage.tsx");
    expect(page).toContain("new URLSearchParams(params)");
    expect(page).not.toContain('"#/eval?" +');
  });

  it("evalHash：query 参数往返（+ 与 / 都不坏）", async () => {
    const { evalHash } = await import("../src/pages/EvalPage");
    const h = evalHash({ run: "20260825-130647-B5+L1/L1-gas-block/run1" });
    const query = h.split("?")[1] ?? "";
    expect(new URLSearchParams(query).get("run"))
      .toBe("20260825-130647-B5+L1/L1-gas-block/run1");
    expect(h.startsWith("#/eval?")).toBe(true);
  });
});
