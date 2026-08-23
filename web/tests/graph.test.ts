/**
 * static/strategy + AST 渲染 + 布局接入（F4/F12）
 *
 * 关键不变式：**图与状态分开** —— 图里必须能看见"一次都没走过的 step"，
 * 只靠转移历史推图会漏掉它们。
 */
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { parseEnvelopeLine, type FlowFrame, type SchemaStatic, type StrategyStatic } from "../src/contract";
import {
  branchExit, matchExitBranch, renderBranches, renderValue, storageKey, vocabOf,
} from "../src/graph/ast";
import { layout } from "../src/graph/layout";

const FIX_DIR = resolve(dirname(fileURLToPath(import.meta.url)), "..", "public", "fixtures");

function read(file: string): { graph: StrategyStatic; flows: FlowFrame[]; schema: SchemaStatic | null } {
  let graph: StrategyStatic | null = null;
  let schema: SchemaStatic | null = null;
  const flows: FlowFrame[] = [];
  for (const line of readFileSync(resolve(FIX_DIR, file), "utf8").split("\n")) {
    if (line.trim() === "") continue;
    const env = parseEnvelopeLine(line);
    if (env.topic === "static/strategy") graph = env.payload;
    if (env.topic === "static/schema") schema = env.payload;
    if (env.topic === "frame/flow") flows.push(env.payload);
  }
  if (!graph) throw new Error("夹具里没有 static/strategy");
  return { graph, flows, schema };
}

/** 转成新布局的输入形状（与 FlowPage 同款） */
function laidOf(graph: StrategyStatic) {
  return layout(
    graph.steps.map((s) => ({ id: s.step_id, branchCount: s.branches.length })),
    graph.edges as { from: string; to: string; kind: string; reason: string }[],
    graph.initial_step,
  );
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
    const laid = laidOf(graph);
    expect(laid.back.size, "armor_hop ⇄ inf_hop 应形成回边").toBeGreaterThan(0);
    for (const key of laid.back) {
      expect(laid.lanes.get(key), `回边 ${key} 应有车道号`).toBeDefined();
    }
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

  it("布局：起点在第 0 层，所有节点都有位置", () => {
    const { graph } = read("leapfrog.jsonl");
    const laid = laidOf(graph);
    expect(laid.layer.get(graph.initial_step)).toBe(0);
    for (const s of graph.steps) {
      expect(laid.positions.get(s.step_id), s.step_id).toBeDefined();
    }
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
  it("无 schema 时退回 identifier（词表没到也不瞎编），前缀用空格不加点", () => {
    expect(renderValue({ op: ">=", args: [{ op: "group_count", group: "inf" }, { param: "min_inf" }] }))
      .toBe("group_count(group=inf) >= 参数 min_inf");
    expect(renderValue({ op: "and", args: [{ op: "a" }, { op: "b" }] })).toBe("a() 且 b()");
    expect(renderValue({ ref: "front" })).toBe("别名 front");
    expect(renderValue({ var: "checkpoint" })).toBe("变量 checkpoint");
  });

  it("嵌套逻辑加括号，避免读错优先级", () => {
    const s = renderValue({ op: "and", args: [{ op: "or", args: [1, 2] }, 3] });
    expect(s).toBe("(1 或 2) 且 3");
  });

  it("I1：有 schema 时谓词/运算符用后端 name_zh（rev 12，单一真相源在后端）", () => {
    const { schema } = read("leapfrog.jsonl");
    const vocab = vocabOf(schema);
    expect(vocab.pred["group_count"]).toBe("组内数量");
    expect(vocab.ops[">="]).toBe("≥");
    expect(
      renderValue({ op: ">=", args: [{ op: "group_count", group: "inf" }, { param: "min_inf" }] }, 0, vocab),
    ).toBe("组内数量(group=inf) ≥ 参数 min_inf");
    expect(
      renderValue({ op: "arrived", group: "armor", target: { ref: "front" }, radius: 3.5 }, 0, vocab),
    ).toBe("已抵达(group=armor, target=别名 front, radius=3.5)");
    expect(vocab.acts["attack_move_to"]).toBe("攻击移动");
  });

  it("I2：夹具的 static/strategy 带可读名 —— 策略名、step 名、reason 与组名都有 zh", () => {
    const { graph } = read("leapfrog.jsonl");
    expect(graph.display_name_zh).toBe("装甲蛙跳推进");
    expect(graph.description_zh.length).toBeGreaterThan(0);
    const garrison = graph.steps.find((s) => s.step_id === "garrison")!;
    expect(garrison.display_name_zh).toBe("驻守集结");
    expect(graph.reasons?.["READY"]).toBe("集结就绪");
    expect(graph.group_names?.["G_TANK"]).toBe("装甲组");
    // 没写 zh 的 step 退回 identifier（契约 default("")，不炸）
    expect(graph.steps.every((s) => (s.display_name_zh || s.step_id) === s.display_name_zh
      || s.display_name_zh === "")).toBe(true);
  });

  it("真夹具的分支能渲染出条件与动作（zh 词表生效）", () => {
    const { graph, schema } = read("leapfrog.jsonl");
    // garrison 的条件带 group_count（组内数量）；armor_hop 的动作带 attack_move_to（攻击移动）
    const garrison = renderBranches(graph.steps.find((s) => s.step_id === "garrison")!.branches, schema);
    const hop = renderBranches(graph.steps.find(
      (s) => s.branches.some((b) =>
        (b["do"] as Record<string, unknown>[] | undefined)?.some((a) => a["op"] === "group_action")))!
      .branches, schema);
    expect(garrison.some((r) => r.when !== null && r.when.includes("组内数量"))).toBe(true);
    expect(garrison.some((r) => r.when === null), "应有一个 else 分支").toBe(true);
    expect(hop.some((r) => r.actions.some((a) => a.text.includes("攻击移动")))).toBe(true);
    // I1 的端到端验收：别名真的流到了渲染层，条件里不再出现裸谓词名
    expect(garrison.some((r) => r.when !== null && r.when.includes("group_count"))).toBe(false);
  });

  it("被后端标为不可用的 do op 带上原因", () => {
    const rows = renderBranches(
      [{ branch_id: "x", do: [{ op: "start_timer", name: "t" }] }],
      { forbidden: { do_ops: { start_timer: "计时器未实现" } } } as never,
    );
    expect(rows[0]!.actions[0]!.forbidden).toBe("计时器未实现");
  });
});

describe("F12：边锚定与位置持久化", () => {
  it("matchExitBranch：每条边都能找到自己的 branch 行（锚定的前提）", () => {
    const { graph } = read("leapfrog.jsonl");
    for (const e of graph.edges as { from: string; to: string; kind: string; reason: string }[]) {
      const step = graph.steps.find((s) => s.step_id === e.from)!;
      const idx = matchExitBranch(step.branches, e);
      expect(idx, `边 ${e.from}→${e.to} 找不到对应 branch`).not.toBeNull();
      expect(idx!).toBeGreaterThanOrEqual(0);
    }
  });

  it("matchExitBranch：匹配不上返回 null（不猜，调用方退回节点中心锚）", () => {
    expect(matchExitBranch([], { kind: "done", reason: "X" })).toBeNull();
    expect(matchExitBranch(
      [{ do: [{ op: "exit_step", kind: "done", reason: "READY" }] }],
      { kind: "done", reason: "OTHER" },
    )).toBeNull();
  });

  it("storageKey 含 version：换 version 不复用旧坐标（重编译的策略结构已变）", () => {
    expect(storageKey("s", 1)).toBe("flow-node-pos:s@1");
    expect(storageKey("s", 2)).not.toBe(storageKey("s", 1));
  });
});

describe("branchExit：DSL 的两种 exit 必须分得开（2026-08-21 审查发现）", () => {
  const mk = (dos: Record<string, unknown>[]) => ({ do: dos } as Record<string, unknown>);

  it("exit_step → 转场（有边）", () => {
    const ex = branchExit(mk([{ op: "exit_step", kind: "done", reason: "READY" }]));
    expect(ex.kind).toBe("step");
    expect(ex.kind === "step" && ex.reason).toBe("READY");
  });

  it("exit_strategy → 终局（无边），不能被当成「留在本步」", () => {
    const ex = branchExit(mk([{ op: "exit_strategy", kind: "done", reason: "ARRIVED" }]));
    expect(ex.kind).toBe("end");
    expect(ex.kind === "end" && ex.reason).toBe("ARRIVED");
  });

  it("没有 exit → 留在本步", () => {
    expect(branchExit(mk([{ op: "group_action" }])).kind).toBe("stay");
    expect(branchExit(mk([])).kind).toBe("stay");
    expect(branchExit(undefined).kind).toBe("stay");
  });

  it("matchExitBranch 只认 exit_step —— exit_strategy 不抢边", () => {
    const branches = [
      mk([{ op: "exit_strategy", kind: "done", reason: "X" }]),
      mk([{ op: "exit_step", kind: "done", reason: "X" }]),
    ];
    expect(matchExitBranch(branches, { kind: "done", reason: "X" })).toBe(1);
  });
});
