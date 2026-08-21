/**
 * F12c：Sugiyama-lite 分层布局 —— 破环 / 最长路径分层 / barycenter 降交叉 / 回边车道。
 *
 * 注：PLAN 里这个文件叫 layout.test.ts，但该名字已被 F10 的外壳布局测试占用 ——
 * 图布局测试落在这里（graph-layout.test.ts）。
 */
import { describe, expect, it } from "vitest";
import {
  GAP_Y, LANE_H, backDip, bfsOrder, countCrossings, edgeKey, layout, nodeHeight,
  type GraphEdge, type LayoutNode,
} from "../src/graph/layout";

const n = (id: string, branchCount = 2): LayoutNode => ({ id, branchCount });
const e = (from: string, to: string): GraphEdge => ({ from, to, kind: "done", reason: "R" });

describe("破环与回边", () => {
  it("环图不崩：回边被标出、拿到车道号，且不参与分层（层号单调）", () => {
    // 蛙跳骨架：garrison → armor_hop → inf_hop → armor_hop（环）
    const nodes = [n("garrison"), n("armor_hop"), n("inf_hop")];
    const edges = [
      e("garrison", "armor_hop"),
      e("armor_hop", "inf_hop"),
      e("inf_hop", "armor_hop"),
    ];
    const laid = layout(nodes, edges, "garrison");
    expect(laid.layer.get("garrison")).toBe(0);
    expect(laid.layer.get("armor_hop")).toBe(1);
    expect(laid.layer.get("inf_hop")).toBe(2);
    const backKey = edgeKey("inf_hop", "armor_hop");
    expect(laid.back.has(backKey), "inf_hop→armor_hop 是回边").toBe(true);
    expect(laid.lanes.get(backKey)).toBeDefined();
  });

  it("回边车道：沉降 y 在两端点之下，车道号越大沉得越深（多条回边不互压）", () => {
    const dip0 = backDip(100, 80, 0);
    const dip2 = backDip(100, 80, 2);
    expect(dip0).toBeGreaterThan(100);
    expect(dip2).toBeGreaterThan(dip0);
    expect(dip2 - dip0).toBe(2 * LANE_H);
  });

  it("同较低层的多条回边各占一个车道（车道号互不重复）", () => {
    // 两个环共享较低层：b→a 与 c→a
    const nodes = [n("a"), n("b"), n("c")];
    const edges = [e("a", "b"), e("b", "a"), e("a", "c"), e("c", "a")];
    const laid = layout(nodes, edges, "a");
    const laneB = laid.lanes.get(edgeKey("b", "a"));
    const laneC = laid.lanes.get(edgeKey("c", "a"));
    expect(laneB).toBeDefined();
    expect(laneC).toBeDefined();
    expect(laneB).not.toBe(laneC);
  });
});

describe("降交叉（barycenter）", () => {
  it("构造图：barycenter 序的交叉数 <= BFS 基线序（这里是严格更优）", () => {
    // BFS 发现序会造成 1 个交叉；barycenter 把 L2 排成 d,c 后交叉归零
    const nodes = [n("s"), n("a"), n("b"), n("c"), n("d")];
    const edges = [
      e("s", "a"), e("s", "b"),
      e("a", "c"), e("a", "d"), e("b", "c"),
    ];
    const bfs = bfsOrder(nodes, edges, "s");
    const laid = layout(nodes, edges, "s");
    const bfsCross = countCrossings(edges, bfs.layer, bfs.order);
    const baryCross = countCrossings(edges, laid.layer, laid.order);
    expect(bfsCross).toBeGreaterThan(0);
    expect(baryCross).toBeLessThanOrEqual(bfsCross);
  });
});

describe("节点几何与不可达", () => {
  it("节点高度随 branchCount 增长；同层节点的 y 按「高度 + 间距」累加（锚边的前提）", () => {
    expect(nodeHeight(3)).toBeGreaterThan(nodeHeight(0));
    // s→a、s→b：a/b 同层（都从 initial 可达且路径等长），层内序按 id 稳定排
    const nodes = [n("s"), n("a", 1), n("b", 3)];
    const edges = [e("s", "a"), e("s", "b")];
    const laid = layout(nodes, edges, "s");
    const pa = laid.positions.get("a")!;
    const pb = laid.positions.get("b")!;
    expect(laid.layer.get("a")).toBe(laid.layer.get("b"));
    expect(pb.y).toBeCloseTo(pa.y + nodeHeight(1) + GAP_Y, 5);
  });

  it("不可达节点仍被布出（排到末尾各自成层，热改/手构造时也要看得见）", () => {
    const nodes = [n("i"), n("r"), n("orphan")];
    const edges = [e("i", "r")];
    const laid = layout(nodes, edges, "i");
    const orphan = laid.positions.get("orphan");
    expect(orphan).toBeDefined();
    expect(laid.layer.get("orphan")!).toBeGreaterThan(laid.layer.get("r")!);
  });

  it("内容尺寸覆盖所有节点（PanZoom 的 fit 依赖它）", () => {
    const nodes = [n("a", 2), n("b", 5)];
    const laid = layout(nodes, [e("a", "b")], "a");
    const pb = laid.positions.get("b")!;
    expect(laid.size.w).toBeGreaterThanOrEqual(pb.x + 210);
    expect(laid.size.h).toBeGreaterThanOrEqual(pb.y + nodeHeight(5));
  });
});
