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

/**
 * F12b：边 ↔ branch 的对应关系（修根因 M：branch 才是边，边要锄在它所在的 branch 行上）。
 *
 * 编译期已保证：每条声明边 (kind, reason) 与某个 exit_step/exit_strategy 分支一一对应
 * （后端 schema.py 的 EdgeView 注释）。这里按同规则匹配；匹配不上（热改中途/异常数据）
 * 返回 null —— 调用方退回节点中心锚，不猜。
 */
export function matchExitBranch(
  branches: Record<string, unknown>[],
  edge: { kind: string; reason: string },
): number | null {
  for (let i = 0; i < branches.length; i += 1) {
    const b = branches[i]!;
    const dos = Array.isArray(b["do"]) ? (b["do"] as Record<string, unknown>[]) : [];
    const exit = dos.find((a) => a["op"] === "exit_step" || a["op"] === "exit_strategy");
    if (exit && String(exit["kind"]) === edge.kind && String(exit["reason"]) === edge.reason) {
      return i;
    }
  }
  return null;
}

/**
 * F12a：节点拖动位置的持久化键 —— **必须带 version**。
 * 不带的话重编译的策略（结构已变）会继承过期坐标，节点飘回旧位置。
 */
export function storageKey(strategyId: string, version: number): string {
  return `flow-node-pos:${strategyId}@${version}`;
}
