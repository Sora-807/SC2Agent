/**
 * 规划页（F9/F13）—— 离线三工作台。
 *
 * 红线（ADR-0022 反例 / R5）：
 * 1. 离线草稿**绝不默认叠加 live** —— 本页不读 live 会话，只读静态面；
 * 2. live（实时驾驶模式）中不出现模块/Strategy 的创建与编辑入口（R5）——
 *    守卫读**模式轴**（F13 修根因 W：旧代码读 sourceKind==="live"，但那个值永远产生不出来）；
 * 3. 所有画布操作转成**结构化草稿**（DraftItem / hunk），人与 agent 同一表示。
 */
import { useMemo, useState } from "react";
import { createProposal } from "../api/proposals";
import { renderBranches, renderValue } from "../graph/ast";
import { layout } from "../graph/layout";
import {
  describeItem, draftCost, draftToHunks, emptyItem, placementOptions,
  type DraftItem,
} from "../planning/queue-draft";
import { PLAN_GATE_REASON } from "../shell/rail";
import { Card, Empty, PAGE_SCROLL } from "../shell/ui";
import { useFrames } from "../store/frames";
import type { CatalogStatic, MapStatic, SchemaStatic, StrategyStatic } from "../contract";

const TABS = [
  ["map", "地图规划"],
  ["production", "生产规划"],
  ["flow", "Flow 装配"],
] as const;

export function PlanningPage(props: { initialTab?: "map" | "production" | "flow" }) {
  const { catalog, map, strategy, schema, api, mode } = useFrames();
  const [tab, setTab] = useState<"map" | "production" | "flow">(
    props.initialTab ?? "production",
  );
  // R5 门控：实时驾驶（live）下 authoring 全部置灰 + 理由（G7 不静默隐藏）
  const gated = mode === "drive";

  return (
    <div className={PAGE_SCROLL + " space-y-3"}>
      <Card title="规划（离线工作台）"
            right={<span className={gated ? "text-note text-red-400" : "text-note text-amber-500"}>
              {gated
                ? PLAN_GATE_REASON + " —— 切回离线编辑或复盘模式后再改"
                : "草稿只在本页；提为提案后才进入审批流（§6）"}
            </span>}>
        <div className="flex gap-2 text-xs">
          {TABS.map(([k, label]) => (
            <button key={k} onClick={() => setTab(k)} disabled={gated}
                    title={gated ? PLAN_GATE_REASON : undefined}
                    className={"rounded border px-2 py-1 "
                      + (gated ? "cursor-not-allowed border-neutral-800 text-ghost" : "")
                      + (!gated && tab === k ? "border-neutral-500" : "")
                      + (!gated && tab !== k ? "border-neutral-800 text-faint" : "")}>
              {label}
            </button>
          ))}
        </div>
      </Card>

      {!gated && tab === "map" && <MapPlanning map={map} />}
      {!gated && tab === "production" && (
        <ProductionPlanning catalog={catalog} map={map} apiOk={api.ok} />
      )}
      {!gated && tab === "flow" && <FlowAssembly graph={strategy} schema={schema} />}
      {gated && (
        <Card title="实时驾驶中不可编辑">
          <Empty text={PLAN_GATE_REASON + " —— 切回「离线编辑」或「复盘」模式后再来"} />
        </Card>
      )}
    </div>
  );
}

function MapPlanning(props: { map: MapStatic | null }) {
  const { map } = props;
  if (!map) return <Card title="地图规划"><Empty text="等 static/map…" /></Card>;
  return (
    <div className="grid grid-cols-1 gap-3 lg:grid-cols-3">
      <Card title="建造槽位">
        <ul className="max-h-72 space-y-1 overflow-auto">
          {map.build_slots.map((s) => (
            <li key={s.name} className="text-neutral-300">
              <b>{s.name}</b> <span className="text-faint">{s.kind} {s.size}×{s.size}</span>
              <span className="ml-2 text-note text-ghost">
                tl {s.tl.join(",")} → br {s.br.join(",")}
              </span>
            </li>
          ))}
        </ul>
        <div className="mt-1 text-note text-ghost">
          br / build_point / reported_position 由后端按 ADR-0027 算好 —— 前端零几何换算。
        </div>
      </Card>
      <Card title="点位与区域">
        <div className="text-dim">点位（PosMark）</div>
        <ul className="space-y-1">
          {map.pos_marks.map((m) => (
            <li key={m.name} className="text-neutral-300">
              <b>{m.name}</b> <span className="text-faint">({m.pos.join(", ")})</span>
              {m.description_zh && <span className="ml-1 text-note text-ghost">{m.description_zh}</span>}
            </li>
          ))}
        </ul>
        <div className="mt-2 text-dim">区域（leaf）</div>
        <ul className="space-y-1">
          {map.regions.leaf.map((r) => (
            <li key={r.stable_id} className="text-neutral-300">
              <b>{r.display_name_zh}</b> <span className="text-faint">({r.stable_id})</span>
              {r.build_slots.length > 0 && (
                <span className="ml-1 text-note text-ghost">槽位 {r.build_slots.join(", ")}</span>
              )}
            </li>
          ))}
        </ul>
      </Card>
      <Card title="放置语法速查">
        <pre className="rounded bg-neutral-950 p-2 text-note text-neutral-300">
          {'{ kind: "exact", mark: "rax_1" }\n{ kind: "in_region", region: "main_build" }'}
        </pre>
        <div className="mt-1 text-note text-ghost">
          没有 placement 的 build 在编译期就非法。地形
          {map.terrain ? "已下发（可在地图页看）" : "未下发（sim/离线为纯色底）"}。
        </div>
      </Card>
    </div>
  );
}

function ProductionPlanning(props: {
  catalog: CatalogStatic | null;
  map: MapStatic | null;
  apiOk: boolean;
}) {
  const { catalog, map, apiOk } = props;
  const [items, setItems] = useState<DraftItem[]>([]);
  const [title, setTitle] = useState("生产草稿");
  const [rationale, setRationale] = useState("");
  const [msg, setMsg] = useState<string | null>(null);

  const options = useMemo(() => placementOptions(map), [map]);
  const cost = useMemo(() => draftCost(items, catalog), [items, catalog]);
  const buildables = (catalog?.entries ?? []).filter(
    (e: CatalogStatic["entries"][number]) =>
      e.role === "building" || e.role === "combat" || e.role === "worker");

  const update = (id: string, patch: Partial<DraftItem>): void =>
    setItems((list) => list.map((it) => (it.id === id ? { ...it, ...patch } : it)));

  const setPlacement = (id: string, value: string): void => {
    if (!value) { update(id, { placement: null }); return; }
    const [kind, name] = value.split(":");
    const region = name ?? "";
    update(id, {
      placement: kind === "region"
        ? { kind: "in_region", region, index: null }
        : { kind: "exact", mark: region },
    });
  };

  const submit = async (): Promise<void> => {
    if (!apiOk) { setMsg("后端未连接，无法提交提案（可继续本地编辑）"); return; }
    if (items.length === 0) { setMsg("草稿是空的"); return; }
    if (!rationale.trim()) { setMsg("必须写理由（§6 P3：没有理由的提案不可接受）"); return; }
    setMsg(null);
    try {
      const p = await createProposal({
        kind: "production_queue",
        title_zh: title || "生产草稿",
        rationale_zh: rationale,
        target: { queue: "main" },
        hunks: draftToHunks(items),
      });
      setMsg("已提交提案 " + p.id + "（" + p.status + "），去对话栏审批");
    } catch (err) {
      setMsg("提交失败：" + (err as Error).message);
    }
  };

  return (
    <Card title="生产规划" right={<span className="text-note text-faint">
      矿 {cost.minerals} · 气 {cost.vespene} · 供给 {cost.supply}
      {cost.missing.length > 0 && (
        <span className="ml-1 text-amber-400">缺目录项：{cost.missing.join(", ")}</span>
      )}
    </span>}>
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <button className="rounded border border-neutral-700 px-2 py-1 text-xs"
                onClick={() => setItems((l) => [...l, emptyItem()])}>+ 加一项</button>
        <button className="rounded border border-neutral-700 px-2 py-1 text-xs"
                onClick={() => setItems([])}>清空</button>
        <span className="text-note text-ghost">
          build 必须有 placement；train / assign_workers 不需要
        </span>
      </div>

      {items.length === 0 ? <Empty text="还没有草稿项" /> : (
        <div className="space-y-1">
          {items.map((it, i) => (
            <div key={it.id} className="rounded border border-neutral-800 p-1">
              <div className="flex flex-wrap items-center gap-2">
                <span className="w-6 text-ghost">{i}.</span>
                <select value={it.op}
                        onChange={(e) => update(it.id, { op: e.target.value as DraftItem["op"] })}
                        className="rounded border border-neutral-700 bg-neutral-950 px-1 text-xs">
                  <option value="build">build</option>
                  <option value="train">train</option>
                  <option value="assign_workers">assign_workers</option>
                </select>
                {it.op === "assign_workers" ? (
                  <>
                    <select value={it.task ?? "gas"}
                            onChange={(e) => update(it.id, { task: e.target.value as DraftItem["task"] })}
                            className="rounded border border-neutral-700 bg-neutral-950 px-1 text-xs">
                      <option value="mineral">维持采矿</option>
                      <option value="gas">维持采气</option>
                      <option value="idle">维持空闲</option>
                    </select>
                    <input type="number" min={0} value={it.count}
                           onChange={(e) => update(it.id, { count: Number(e.target.value) })}
                           className="w-16 rounded border border-neutral-700 bg-neutral-950 px-1 text-xs" />
                    <span className="text-note text-ghost">目标值（维持 N 个，幂等）</span>
                  </>
                ) : (
                  <>
                    <select value={it.type ?? ""}
                            onChange={(e) => update(it.id, { type: e.target.value || null })}
                            className="rounded border border-neutral-700 bg-neutral-950 px-1 text-xs">
                      <option value="">（选类型）</option>
                      {buildables.map((e) => (
                        <option key={e.stable_id} value={e.stable_id}>
                          {e.display_name_zh}（{e.cost.minerals}/{e.cost.vespene}，{e.build_time}s）
                        </option>
                      ))}
                    </select>
                    <input type="number" min={1} value={it.count}
                           onChange={(e) => update(it.id, { count: Number(e.target.value) })}
                           className="w-16 rounded border border-neutral-700 bg-neutral-950 px-1 text-xs" />
                    <select
                      value={it.placement
                        ? (it.placement.kind === "exact" ? "slot:" + it.placement.mark : "region:" + it.placement.region)
                        : ""}
                      onChange={(e) => setPlacement(it.id, e.target.value)}
                      className="rounded border border-neutral-700 bg-neutral-950 px-1 text-xs">
                      <option value="">（placement）</option>
                      {options.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
                    </select>
                  </>
                )}
                <button className="ml-auto text-red-400"
                        onClick={() => setItems((l) => l.filter((x) => x.id !== it.id))}>×</button>
              </div>
              <div className="mt-0.5 pl-8 text-note text-ghost">{describeItem(it)}</div>
            </div>
          ))}
        </div>
      )}

      <div className="mt-3 space-y-2 border-t border-neutral-800 pt-2">
        <input className="w-64 rounded border border-neutral-700 bg-neutral-950 px-2 py-1 text-xs"
               value={title} onChange={(e) => setTitle(e.target.value)} placeholder="提案标题" />
        <input className="w-full rounded border border-neutral-700 bg-neutral-950 px-2 py-1 text-xs"
               value={rationale} onChange={(e) => setRationale(e.target.value)}
               placeholder="理由（必填）—— 你自己审批自己的草稿也一样要理由（§6 P3）" />
        <button className="rounded border border-emerald-700 bg-emerald-900/40 px-3 py-1 text-emerald-200"
                onClick={() => void submit()}>提为提案（走审批流）</button>
        {msg && <div className="text-xs text-amber-400">{msg}</div>}
      </div>
    </Card>
  );
}

function FlowAssembly(props: {
  graph: StrategyStatic | null;
  schema: SchemaStatic | null;
}) {
  const { graph, schema } = props;
  const [stepId, setStepId] = useState<string | null>(null);

  if (!graph) return <Card title="Flow 装配"><Empty text="等 static/strategy…" /></Card>;
  const laid = layout(
    graph.steps.map((s: { step_id: string; branches: unknown[] }) => ({
      id: s.step_id, branchCount: Array.isArray(s.branches) ? s.branches.length : 0,
    })),
    graph.edges as { from: string; to: string; kind: string; reason: string }[],
    graph.initial_step,
  );
  // 展示顺序 = 布局顺序（层 → 层内序）：F12 起布局在 graph/layout，这里只借它的排序
  const stepOrder = [...graph.steps].sort(
    (x: { step_id: string }, y: { step_id: string }) =>
      (laid.layer.get(x.step_id) ?? 0) - (laid.layer.get(y.step_id) ?? 0)
      || (laid.order.get(x.step_id) ?? 0) - (laid.order.get(y.step_id) ?? 0),
  );
  const step = graph.steps.find((s: { step_id: string }) => s.step_id === (stepId ?? graph.initial_step))
    ?? graph.steps[0];
  const branches = step ? renderBranches(step.branches, schema) : [];

  return (
    <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
      <Card title={"策略图 · " + graph.id + " v" + graph.version}>
        <div className="space-y-1">
          {stepOrder.map((n: { step_id: string }) => (
            <button key={n.step_id} onClick={() => setStepId(n.step_id)}
                    className={"w-full rounded border p-2 text-left "
                      + (n.step_id === step?.step_id ? "border-neutral-500" : "border-neutral-800")}>
              <span className="font-medium text-neutral-200">{n.step_id}</span>
              {n.step_id === graph.initial_step && <span className="ml-2 text-note text-faint">起点</span>}
              <span className="ml-2 text-note text-ghost">
                {graph.edges
                  .filter((e: { from: string }) => e.from === n.step_id)
                  .map((e: { to: string }) => "→" + e.to)
                  .join(" ")}
              </span>
            </button>
          ))}
        </div>
        <div className="mt-2 text-note text-ghost">
          转移上限 {Object.entries(graph.loop_limits).map(([k, v]) => k + "=" + v).join("、") || "—"} ·
          槽位 {graph.group_slots.map((s: string) => s + "→" + (graph.bindings[s] ?? "?")).join("、")}
        </div>
      </Card>
      <Card title={"分支 AST · " + (step?.step_id ?? "—")}>
        {branches.length === 0 ? <Empty text="没有分支" /> : (
          <ol className="space-y-2">
            {branches.map((b) => (
              <li key={b.index} className="rounded border border-neutral-800 p-2">
                <div className="flex gap-2 text-xs">
                  <span className="text-faint">#{b.index}</span>
                  <span>{b.id ?? "（未命名）"}</span>
                  <span className="text-faint">{b.when === null ? "else" : "when"}</span>
                </div>
                {b.when !== null && <code className="mt-1 block text-sky-300">{b.when}</code>}
                {b.actions.length > 0 && (
                  <ul className="mt-1 text-xs text-neutral-300">
                    {b.actions.map((a, i) => (
                      <li key={i} className={a.forbidden ? "text-ghost" : ""}>
                        → {a.text}
                        {a.forbidden && <span className="ml-1 text-amber-600">（不可用：{a.forbidden}）</span>}
                      </li>
                    ))}
                  </ul>
                )}
              </li>
            ))}
          </ol>
        )}
      </Card>
      <Card title="声明与别名" className="lg:col-span-2">
        <div className="grid grid-cols-1 gap-2 md:grid-cols-2">
          <div>
            <div className="text-dim">参数声明</div>
            {Object.entries(graph.params).map(([k, v]) => (
              <div key={k} className="text-xs">
                {k} : {String((v as Record<string, unknown>)["type"])}
                {" = "}{JSON.stringify((v as Record<string, unknown>)["default"])}
              </div>
            ))}
          </div>
          <div>
            <div className="text-dim">别名（definitions）</div>
            {Object.entries(graph.definitions).map(([k, v]) => (
              <div key={k} className="text-xs">{k} = <code>{renderValue(v)}</code></div>
            ))}
            {Object.keys(graph.definitions).length === 0 && <Empty text="无" />}
          </div>
        </div>
        <div className="mt-2 border-t border-neutral-800 pt-2 text-note text-ghost">
          AST 编辑（改结构）按决策 U8 留到 next —— 现在先把「看与导出」做扎实；
          导出按钮把 static/strategy 原样存为 JSON。
        </div>
        <button
          className="mt-1 rounded border border-neutral-700 px-2 py-1 text-xs"
          onClick={() => {
            const blob = new Blob([JSON.stringify(graph, null, 2)], { type: "application/json" });
            const url = URL.createObjectURL(blob);
            const a = document.createElement("a");
            a.href = url;
            a.download = graph.id + "-v" + graph.version + ".json";
            a.click();
            URL.revokeObjectURL(url);
          }}
        >导出 JSON</button>
      </Card>
    </div>
  );
}