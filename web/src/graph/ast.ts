/**
 * 策略 AST → 人类可读文本。
 *
 * 词表是闭集且由后端下发（`static/schema`），所以这里只做**结构到文本**的映射，
 * 不判断合法性 —— 合法性是编译器的事，前端重写一份只会两边不一致。
 * F4 用它显示分支条件；F9 的编辑器会复用同一套结构认知。
 */
import type { SchemaStatic } from "../contract";

type Node = unknown;

const isRec = (v: unknown): v is Record<string, unknown> =>
  typeof v === "object" && v !== null && !Array.isArray(v);

/** 运算符用中缀显示；谓词用 `名(参数=值)`；引用节点用中文标注 */
export function renderValue(node: Node, depth = 0): string {
  if (node === null || node === undefined) return "null";
  if (typeof node === "number" || typeof node === "boolean") return String(node);
  if (typeof node === "string") return node;
  if (Array.isArray(node)) return "[" + node.map((v) => renderValue(v, depth + 1)).join(", ") + "]";
  if (!isRec(node)) return String(node);

  if ("param" in node) return "参数." + String(node["param"]);
  if ("var" in node) return "变量." + String(node["var"]);
  if ("ref" in node) return "别名." + String(node["ref"]);
  if ("const" in node) return renderValue(node["const"], depth + 1);

  const op = node["op"];
  if (typeof op !== "string") return JSON.stringify(node);

  const args = node["args"];
  if (Array.isArray(args)) {
    const parts = args.map((a) => renderValue(a, depth + 1));
    if (op === "not") return "非(" + parts.join("") + ")";
    if (op === "and" || op === "or") {
      const join = op === "and" ? " 且 " : " 或 ";
      const body = parts.join(join);
      return depth > 0 ? "(" + body + ")" : body;
    }
    return parts.join(" " + op + " ");
  }

  // 命名参数谓词：{op: arrived, group: inf, target: ..., radius: 8}
  const named = Object.entries(node)
    .filter(([k]) => k !== "op")
    .map(([k, v]) => k + "=" + renderValue(v, depth + 1));
  return op + "(" + named.join(", ") + ")";
}

export interface RenderedBranch {
  id: string | null;
  index: number;
  /** null = else 分支（无 when，只能放最后） */
  when: string | null;
  actions: { text: string; forbidden: string | null }[];
}

/** 一个 step 的 branches → 可显示结构；被后端标为不可用的 op 带上原因 */
export function renderBranches(
  branches: Record<string, unknown>[],
  schema: SchemaStatic | null,
): RenderedBranch[] {
  const forbiddenDo: Record<string, string> = schema?.forbidden["do_ops"] ?? {};
  return branches.map((b, index) => ({
    id: typeof b["branch_id"] === "string" ? b["branch_id"] : null,
    index,
    when: "when" in b ? renderValue(b["when"]) : null,
    actions: (Array.isArray(b["do"]) ? (b["do"] as Record<string, unknown>[]) : []).map((a) => ({
      text: renderAction(a),
      forbidden: typeof a["op"] === "string" ? forbiddenDo[a["op"]] ?? null : null,
    })),
  }));
}

function renderAction(a: Record<string, unknown>): string {
  const op = String(a["op"] ?? "?");
  if (op === "exit_step" || op === "exit_strategy") {
    return op + "(" + String(a["kind"]) + "/" + String(a["reason"]) + ")";
  }
  if (op === "group_action") {
    const params = isRec(a["params"])
      ? Object.entries(a["params"]).map(([k, v]) => k + "=" + renderValue(v, 1)).join(", ")
      : "";
    return `${String(a["group_slot"])}·${String(a["type"])} → ${String(a["action_atom"])}(${params})`;
  }
  if (op === "set_variable" || op === "set_local") {
    return op + " " + String(a["name"]) + " = " + renderValue(a["value"], 1);
  }
  return op;
}

/** 分层布局：从 initial_step 做 BFS 定列，同列纵向排开；回边单独标出 */
export interface Laid {
  nodes: { id: string; col: number; row: number }[];
  edges: { from: string; to: string; kind: string; reason: string; back: boolean }[];
  cols: number;
  rows: number;
}

export function layout(
  steps: { step_id: string }[],
  edges: { from: string; to: string; kind: string; reason: string }[],
  initial: string,
): Laid {
  const ids = steps.map((s) => s.step_id);
  const out = new Map<string, string[]>();
  for (const e of edges) {
    if (!out.has(e.from)) out.set(e.from, []);
    out.get(e.from)!.push(e.to);
  }
  const depth = new Map<string, number>();
  const queue: string[] = ids.includes(initial) ? [initial] : [...ids];
  if (queue.length > 0) depth.set(queue[0]!, 0);
  while (queue.length > 0) {
    const cur = queue.shift()!;
    for (const nxt of out.get(cur) ?? []) {
      if (depth.has(nxt)) continue;         // 已定层 → 后面那条是回边
      depth.set(nxt, (depth.get(cur) ?? 0) + 1);
      queue.push(nxt);
    }
  }
  // 不可达的 step 也要画出来（编译器会拒不可达，但热改/手构造时要能看见）
  let orphanCol = Math.max(0, ...[...depth.values()]) + 1;
  for (const id of ids) {
    if (!depth.has(id)) depth.set(id, orphanCol++);
  }
  const byCol = new Map<number, string[]>();
  for (const id of ids) {
    const c = depth.get(id) ?? 0;
    if (!byCol.has(c)) byCol.set(c, []);
    byCol.get(c)!.push(id);
  }
  const nodes = ids.map((id) => {
    const c = depth.get(id) ?? 0;
    return { id, col: c, row: (byCol.get(c) ?? []).indexOf(id) };
  });
  return {
    nodes,
    edges: edges.map((e) => ({
      ...e,
      back: (depth.get(e.to) ?? 0) <= (depth.get(e.from) ?? 0),
    })),
    cols: Math.max(1, ...nodes.map((n) => n.col + 1)),
    rows: Math.max(1, ...[...byCol.values()].map((v) => v.length)),
  };
}
