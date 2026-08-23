/**
 * 策略 AST → 人类可读文本。
 *
 * 词表是闭集且由后端下发（`static/schema`），所以这里只做**结构到文本**的映射，
 * 不判断合法性 —— 合法性是编译器的事，前端重写一份只会两边不一致。
 * F4 用它显示分支条件；F9 的编辑器会复用同一套结构认知。
 *
 * 中文名（I1/I4，rev 12）：谓词/运算符/动作的 zh 从 `static/schema` 的 name_zh 读，
 * 前端不抄第二份词表（红线 C4）。没拿到 schema 时退回 identifier，行为同旧版。
 */
import type { SchemaStatic } from "../contract";

type Node = unknown;

const isRec = (v: unknown): v is Record<string, unknown> =>
  typeof v === "object" && v !== null && !Array.isArray(v);

/** 词表中文名查找表（vocabOf 从 schema 建出来；空表 = 无 schema，退回 identifier） */
export interface Vocab {
  pred: Record<string, string>;
  ops: Record<string, string>;
  acts: Record<string, string>;
}

export const EMPTY_VOCAB: Vocab = { pred: {}, ops: {}, acts: {} };

export function vocabOf(schema: SchemaStatic | null): Vocab {
  if (!schema?.predicates) return EMPTY_VOCAB;
  return {
    pred: Object.fromEntries(
      Object.entries(schema.predicates).map(([k, v]) => [k, v.name_zh || k]),
    ),
    ops: Object.fromEntries(
      Object.entries(schema.operators ?? {}).map(([k, v]) => [k, v.name_zh || k]),
    ),
    acts: Object.fromEntries(
      Object.entries(schema.actions ?? {}).map(([k, v]) => [k, v.name_zh || k]),
    ),
  };
}

/** 运算符用中缀显示（rev 12 起用词表中文名，如 ≥）；谓词用 `中文名(参数=值)`；引用节点用中文标注 */
export function renderValue(node: Node, depth = 0, vocab: Vocab = EMPTY_VOCAB): string {
  if (node === null || node === undefined) return "null";
  if (typeof node === "number" || typeof node === "boolean") return String(node);
  if (typeof node === "string") return node;
  if (Array.isArray(node)) return "[" + node.map((v) => renderValue(v, depth + 1, vocab)).join(", ") + "]";
  if (!isRec(node)) return String(node);

  if ("param" in node) return "参数 " + String(node["param"]);
  if ("var" in node) return "变量 " + String(node["var"]);
  if ("ref" in node) return "别名 " + String(node["ref"]);
  if ("const" in node) return renderValue(node["const"], depth + 1, vocab);

  const op = node["op"];
  if (typeof op !== "string") return JSON.stringify(node);

  const args = node["args"];
  if (Array.isArray(args)) {
    const parts = args.map((a) => renderValue(a, depth + 1, vocab));
    if (op === "not") return "非(" + parts.join("") + ")";
    if (op === "and" || op === "or") {
      const join = op === "and" ? " 且 " : " 或 ";
      const body = parts.join(join);
      return depth > 0 ? "(" + body + ")" : body;
    }
    return parts.join(" " + (vocab.ops[op] ?? op) + " ");
  }

  // 命名参数谓词：{op: arrived, group: inf, target: ..., radius: 8} → 已抵达(group=inf, …)
  const named = Object.entries(node)
    .filter(([k]) => k !== "op")
    .map(([k, v]) => k + "=" + renderValue(v, depth + 1, vocab));
  return (vocab.pred[op] ?? op) + "(" + named.join(", ") + ")";
}

export interface RenderedBranch {
  id: string | null;
  /** 分支中文别名（rev 15，模板/手写皆可声明）；null = 没写 */
  nameZh: string | null;
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
  const vocab = vocabOf(schema);
  return branches.map((b, index) => ({
    id: typeof b["branch_id"] === "string" ? b["branch_id"] : null,
    nameZh: typeof b["display_name_zh"] === "string" && b["display_name_zh"]
      ? b["display_name_zh"] : null,
    index,
    when: "when" in b ? renderValue(b["when"], 0, vocab) : null,
    actions: (Array.isArray(b["do"]) ? (b["do"] as Record<string, unknown>[]) : []).map((a) => ({
      text: renderAction(a, vocab),
      forbidden: typeof a["op"] === "string" ? forbiddenDo[a["op"]] ?? null : null,
    })),
  }));
}

function renderAction(a: Record<string, unknown>, vocab: Vocab = EMPTY_VOCAB): string {
  const op = String(a["op"] ?? "?");
  if (op === "exit_step" || op === "exit_strategy") {
    return (op === "exit_step" ? "转场" : "结束策略")
      + "(" + String(a["kind"]) + "/" + String(a["reason"]) + ")";
  }
  if (op === "group_action") {
    const params = isRec(a["params"])
      ? Object.entries(a["params"]).map(([k, v]) => k + "=" + renderValue(v, 1, vocab)).join(", ")
      : "";
    const atom = String(a["action_atom"]);
    return `${String(a["group_slot"])}·${String(a["type"])} → ${vocab.acts[atom] ?? atom}(${params})`;
  }
  if (op === "set_variable" || op === "set_local") {
    return "设变量 " + String(a["name"]) + " = " + renderValue(a["value"], 1, vocab);
  }
  if (op === "start_timer" || op === "stop_timer") {
    return (op === "start_timer" ? "起表 " : "停表 ") + String(a["name"]);
  }
  return op;
}

/**
 * 一个 branch 的**出口语义**。DSL 有两种 exit，图上必须分得开：
 * - `exit_step`      转场：edges 里有对应边 → 去某个 step；
 * - `exit_strategy`  **终局**：edges 里没有边 → 整个策略结束；
 * - 没有 exit         本帧动作做完，下一帧仍在本步求值 → 留在本步。
 *
 * 之前 FlowPage 只判"有没有边"，于是后两者共用一句「留在本步」——
 * 策略图里最重要的终态语义被画成了"继续等待"，真相只能点开详情卡才看得到。
 */
export type BranchExit =
  | { kind: "step"; exitKind: string; reason: string }
  | { kind: "end"; exitKind: string; reason: string }
  | { kind: "stay" };

export function branchExit(branch: Record<string, unknown> | undefined): BranchExit {
  const dos = Array.isArray(branch?.["do"]) ? (branch["do"] as Record<string, unknown>[]) : [];
  for (const a of dos) {
    if (a["op"] === "exit_step") {
      return { kind: "step", exitKind: String(a["kind"]), reason: String(a["reason"]) };
    }
    if (a["op"] === "exit_strategy") {
      return { kind: "end", exitKind: String(a["kind"]), reason: String(a["reason"]) };
    }
  }
  return { kind: "stay" };
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
    // **只认 exit_step**：edges 里的边只由 exit_step 产生，exit_strategy 是终局、没有边。
    // 之前两者一起 find，于是同 step 内 exit_strategy 与 exit_step 的 (kind,reason) 撞车时
    // （编译器并不禁止这种撞车），exit_step 的边会被锚到 exit_strategy 那一行，两行去向互换。
    const exit = dos.find((a) => a["op"] === "exit_step");
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
