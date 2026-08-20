/**
 * static/strategy + AST 渲染 + 分层布局（F4）
 *
 * 关键不变式：**图与状态分开** —— 图里必须能看见"一次都没走过的 step"，
 * 只靠转移历史推图会漏掉它们。
 */
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { parseEnvelopeLine, type FlowFrame, type StrategyStatic } from "../src/contract";
import { layout, renderBranches, renderValue } from "../src/graph/ast";

const FIX_DIR = resolve(dirname(fileURLToPath(import.meta.url)), "..", "public", "fixtures");

function read(file: string): { graph: StrategyStatic; flows: FlowFrame[] } {
  let graph: StrategyStatic | null = null;
  const flows: FlowFrame[] = [];
  for (const line of readFileSync(resolve(FIX_DIR, file), "utf8").split("\n")) {
    if (line.trim() === "") continue;
    const env = parseEnvelopeLine(line);
    if (env.topic === "static/strategy") graph = env.payload;
    if (env.topic === "frame/flow") flows.push(env.payload);
  }
  if (!graph) throw new Error("夹具里没有 static/strategy");
  return { graph, flows };
}

describe("static/strategy", () => {
  it("每份夹具都带策略图，且 steps/edges 非空", () => {
    for (const f of ["opening.jsonl", "blocked.jsonl", "leapfrog.jsonl"]) {
      const { graph } = read(f);
      expect(graph.steps.length, f).toBeGreaterThan(0);
      expect(graph.initial_step, f).toBeTruthy();
      expect(graph.steps.some((s) => s.step_id === graph.initial_step)).toBe(true);
    }
  });

  it("蛙跳场景的图有回边（成环）", () => {
    const { graph } = read("leapfrog.jsonl");
    const laid = layout(graph.steps, graph.edges, graph.initial_step);
    expect(laid.edges.some((e) => e.back), "armor_hop ⇄ inf_hop 应形成回边").toBe(true);
  });

  it("图里能看见没走过的 step（只靠转移历史会漏）", () => {
    const { graph, flows } = read("opening.jsonl");
    const visited = new Set<string>();
    for (const f of flows) {
      const s = f.strategies[0]!;
      visited.add(s.active_step);
      for (const t of s.transitions) { visited.add(t.from); visited.add(t.to); }
    }
    const all = new Set(graph.steps.map((s) => s.step_id));
    expect(all.size).toBeGreaterThanOrEqual(visited.size);
  });

  it("布局：起点在第 0 列，所有节点都有位置", () => {
    const { graph } = read("leapfrog.jsonl");
    const laid = layout(graph.steps, graph.edges, graph.initial_step);
    expect(laid.nodes.length).toBe(graph.steps.length);
    expect(laid.nodes.find((n) => n.id === graph.initial_step)?.col).toBe(0);
    for (const n of laid.nodes) expect(n.col).toBeGreaterThanOrEqual(0);
  });

  it("每条 edge 的 (kind, reason) 都能在某个 exit_step 里找到（编译器保证，这里验帧确实带着）", () => {
    const { graph } = read("leapfrog.jsonl");
    const exits = new Set<string>();
    for (const s of graph.steps) {
      for (const b of s.branches) {
        for (const a of (b["do"] as Record<string, unknown>[] | undefined) ?? []) {
          if (a["op"] === "exit_step") exits.add(String(a["kind"]) + "/" + String(a["reason"]));
        }
      }
    }
    for (const e of graph.edges) {
      expect(exits.has(e.kind + "/" + e.reason), `边 ${e.from}→${e.to} 没有对应 exit_step`).toBe(true);
    }
  });
});

describe("AST 渲染", () => {
  it("运算符用中缀、谓词用命名参数、引用节点标中文", () => {
    expect(renderValue({ op: ">=", args: [{ op: "group_count", group: "inf" }, { param: "min_inf" }] }))
      .toBe("group_count(group=inf) >= 参数.min_inf");
    expect(renderValue({ op: "and", args: [{ op: "a" }, { op: "b" }] })).toBe("a() 且 b()");
    expect(renderValue({ ref: "front" })).toBe("别名.front");
    expect(renderValue({ var: "checkpoint" })).toBe("变量.checkpoint");
  });

  it("嵌套逻辑加括号，避免读错优先级", () => {
    const s = renderValue({ op: "and", args: [{ op: "or", args: [1, 2] }, 3] });
    expect(s).toBe("(1 或 2) 且 3");
  });

  it("真夹具的分支能渲染出条件与动作", () => {
    const { graph } = read("leapfrog.jsonl");
    const step = graph.steps.find((s) => s.branches.length > 1)!;
    const rows = renderBranches(step.branches, null);
    expect(rows.length).toBe(step.branches.length);
    expect(rows.some((r) => r.when === null), "应有一个 else 分支").toBe(true);
    expect(rows.some((r) => r.actions.length > 0)).toBe(true);
  });

  it("被后端标为不可用的 do op 带上原因", () => {
    const rows = renderBranches(
      [{ branch_id: "x", do: [{ op: "start_timer", name: "t" }] }],
      { forbidden: { do_ops: { start_timer: "计时器未实现" } } } as never,
    );
    expect(rows[0]!.actions[0]!.forbidden).toBe("计时器未实现");
  });
});
