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
