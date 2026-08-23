/**
 * 规划页（F9/F13/F16；2026-08-22 左抽屉轮瘦身）—— 离线三工作台的主区部分：
 * 地图 tab = 全幅画布（文件/工具/列表在左侧抽屉 panels/MapPlanSidebar）；
 * 生产 tab = 队列表格 + 投影板（文件/试算在 panels/QueueSidebar）；
 * Flow tab = 策略图装配。工作台状态在 planning/map-plan-store 与 planning/queue-store
 * （模块级 store：抽屉与主区共享，导航/收起不丢）。
 *
 * 红线（ADR-0022 反例 / R5）：
 * 1. 离线草稿**绝不默认叠加 live** —— 本页不读 live 会话，只读静态面；
 * 2. live（游戏模式）中不出现模块/Strategy 的创建与编辑入口（R5）——
 *    守卫读**模式轴**（F13 修根因 W：旧代码读 sourceKind==="live"，但那个值永远产生不出来）；
 * 3. 所有画布操作转成**结构化草稿**（DraftItem / hunk），人与 agent 同一表示。
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { MapCanvas } from "../canvas/MapCanvas";
import { decodeGrid } from "../canvas/grid";
import { defaultLayers, type LayerState } from "../canvas/layers";
import { LayerPanel } from "../canvas/LayerPanel";
import { ProjectionBoard } from "../charts/ProjectionBoard";
import { MapPlanSidebar } from "../panels/MapPlanSidebar";
import { QueueSidebar } from "../panels/QueueSidebar";
import { renderBranches, renderValue, vocabOf } from "../graph/ast";
import { layout } from "../graph/layout";
import {
  nextMarkName, nextSlotName, previewPlacement, snapToCellCenter,
} from "../planning/map-draft";
import { useBaseMap, useMapPlanStore, useMapProj } from "../planning/map-plan-store";
import { useQueueStore } from "../planning/queue-store";
import { QueueTable } from "../planning/QueueTable";
import { placementOptions } from "../planning/queue-draft";
import { PLAN_GATE_REASON } from "../shell/rail";
import { Card, Empty, PAGE_SCROLL } from "../shell/ui";
import { useFrames } from "../store/frames";
import type { SchemaStatic, StrategyStatic } from "../contract";

/**
 * P1 后本组件不再有自己的导航：地图规划/生产规划/Flow 装配是左侧导航栏的三个入口
 * （route: plan-map/plan-production/plan-flow → initialTab），页内的 tab 行已删
 * （用户拍板：导航只有一处）。tab 直接从 prop 派生，不留本地状态。
 */
export function PlanningPage(props: {
  initialTab?: "map" | "production" | "flow";
  /** 深链选中（chat 改动 chip 跳进来带 ?plan=/?map=）—— initOnce 幂等消费 */
  initialPlanId?: string | null;
  initialMapPlanId?: string | null;
}) {
  const { strategy, schema, api, mode } = useFrames();
  const tab = props.initialTab ?? "production";
  // R5 门控：游戏模式下导航已不可达规划页，这里只兜底 hash 直达（G7 不静默）
  const gated = mode === "drive";

  return (
    <div className="flex h-full min-h-0 flex-col gap-2">
      {gated && (
        <div className="text-note text-[color:var(--err-fg)]">
          {PLAN_GATE_REASON} —— 切回规划或复盘模式后再改
        </div>
      )}

      <div className="min-h-0 flex-1">
        {!gated && tab === "map" && (
          /* 十七轮（用户拍板）：地图与工具栏融合成一整块白卡（内部 border-l 分割），
             对话区独立浮起（App 侧留缝 + 分割线），不再各占一列 */
          <div className="flex h-full overflow-hidden rounded-lg border border-l1 bg-panel shadow-sm">
            <div className="min-h-0 flex-1 p-2">
              <MapPlanning apiOk={api.ok} initialId={props.initialMapPlanId} />
            </div>
            <aside className="flex w-72 shrink-0 flex-col border-l border-l1">
              <div className="flex shrink-0 items-center border-b border-l1 py-1.5 pl-3">
                <span className="text-label font-semibold text-strong">规划</span>
              </div>
              <div className="min-h-0 flex-1 space-y-2 overflow-y-auto p-2">
                <MapPlanSidebar />
              </div>
            </aside>
          </div>
        )}
        {!gated && tab === "production" && (
          /* 十九轮（用户拍板）：生产规划改用复盘-生产的三区固定布局
             （上排 队列编辑 | 文件与试算，下方试算投影占满） */
          <ProductionPlanning apiOk={api.ok} initialPlanId={props.initialPlanId} />
        )}
        {!gated && tab === "flow" && (
          <div className={PAGE_SCROLL}>
            <FlowAssembly graph={strategy} schema={schema} />
          </div>
        )}
        {gated && (
          <Card title="游戏中不可编辑">
            <Empty text={PLAN_GATE_REASON + " —— 切回「规划」或「复盘」模式后再来"} />
          </Card>
        )}
      </div>
    </div>
  );
}

/** 地图 tab：画布区。文件/放置工具/点位列表在融合卡右侧栏（MapPlanSidebar）。 */
function MapPlanning(props: { apiOk: boolean; initialId?: string | null }) {
  const catalog = useFrames((s) => s.catalog);
  const st = useMapPlanStore();
  const baseMap = useBaseMap();
  const proj = useMapProj();

  useEffect(() => {
    void useMapPlanStore.getState().initOnce(props.apiOk, props.initialId ?? null);
  }, [props.apiOk]);

  // F16：悬停世界坐标（放置预览用）；离开画布置 null
  const [hoverPos, setHoverPos] = useState<[number, number] | null>(null);
  /** 规划画布图层：初始强开「可建区」与「矿区」（看得见真实矿脉线/预留区才能摆槽位）；
   * 真持有 state —— 用户可经左下角图层面板自行再关（不常用功能，收进折叠小部件） */
  const [planningLayers, setPlanningLayers] = useState<LayerState>(() =>
    ({ ...defaultLayers(), placeable: true, resources: true }));
  const slotError = useRef<string | null>(null);

  // F16：placeable 查询（合成地形已在夹具里）；地形未下发时 null —— 未知不算非法，如实降级
  const placeableAt = useMemo(() => {
    const t = baseMap?.terrain?.placeable;
    if (!t) return null;
    const g = decodeGrid(t);
    return (x: number, y: number): boolean => g.at(Math.floor(x), Math.floor(y)) > 0;
  }, [baseMap]);

  /** 固定建造点预留区（基地/气井/矿脉）：payload 直出，预览/落笔/拖动同用一份 */
  const reserved = baseMap?.reserved ?? null;

  // F16：放置预览（吸附 + 重叠 + placeable）。hover 算 ghost，点击用**同一结果**落笔 ——
  // 预览与落笔永远一致（不会预览绿、点了却拒）。
  const preview = useMemo(() => {
    if (!st.placeMode || !hoverPos) return null;
    if (st.placeMode === "mark") {
      return { kind: "mark" as const, pos: snapToCellCenter(hoverPos), ok: true };
    }
    const pv = previewPlacement({
      mode: "slot", pos: hoverPos, size: st.slotSize, slots: proj.slots, placeableAt, reserved,
    });
    return { kind: "slot" as const, pos: pv.pos, size: st.slotSize, ok: pv.ok };
  }, [st.placeMode, st.slotSize, hoverPos, proj.slots, placeableAt, reserved]);

  const flashError = (msg: string): void => {
    slotError.current = msg;
    setTimeout(() => { slotError.current = null; }, 2600);
  };

  const reservedReason = (kind: string | null): string =>
    kind === "base" ? "压住基地预留位 —— 主基/分矿的指挥中心要建在这"
      : kind === "geyser" ? "压住气井 —— 精炼厂专属位，别的建筑不可占"
        : "压住矿脉 —— 矿区不可占用";

  const place = (pos: [number, number]): void => {
    if (st.placeMode === "mark") {
      const snapped = snapToCellCenter(pos);
      const name = nextMarkName(proj.marks.map((m) => m.name));
      st.pushHunks({ kind: "add_mark", name, pos: snapped });
      st.patch({ selectedName: name });
      return;
    }
    if (st.placeMode === "slot") {
      const pv = previewPlacement({
        mode: "slot", pos, size: st.slotSize, slots: proj.slots, placeableAt, reserved,
      });
      if (!pv.ok) {
        flashError(pv.reason === "overlap"
          ? `与槽位 ${pv.overlaps} 重叠 —— 换个位置`
          : pv.reason === "reserved"
            ? reservedReason(pv.reservedKind)
            : "地形不可建（placeable=0）—— 挪到淡绿可建区");
        return;
      }
      const { name, aliasZh } = nextSlotName(st.slotKind, proj.slots.map((s) => s.name));
      st.pushHunks({ kind: "add_slot", name, pos: pv.pos, size: st.slotSize,
                     slotKind: st.slotKind, aliasZh });
      st.patch({ selectedName: name });
    }
  };

  /** 槽位拖动落点：del+add 两条 hunk（与后端"移动"同语义，且不必为 move_slot 再动契约）。
   *  新位置校验与放置同规则（previewPlacement：重叠 + placeable）。 */
  const dropSlot = (name: string, worldPos: [number, number]): void => {
    const cur = proj.slots.find((s) => s.name === name);
    if (!cur) return;
    const others = proj.slots.filter((s) => s.name !== name);
    const pv = previewPlacement({
      mode: "slot", pos: worldPos, size: cur.size, slots: others, placeableAt, reserved,
    });
    if (Math.abs(cur.pos[0] - pv.pos[0]) < 0.01 && Math.abs(cur.pos[1] - pv.pos[1]) < 0.01) {
      return;   // 没动
    }
    if (!pv.ok) {
      flashError(pv.reason === "overlap"
        ? `与槽位 ${pv.overlaps} 重叠 —— 换个位置`
        : pv.reason === "reserved"
          ? reservedReason(pv.reservedKind)
          : "地形不可建（placeable=0）—— 挪到淡绿可建区");
      return;
    }
    st.pushHunks(
      { kind: "del_slot", name },
      { kind: "add_slot", name, pos: pv.pos, size: cur.size, slotKind: cur.kind,
        aliasZh: cur.aliasZh },
    );
  };

  if (!baseMap) return <Card title="地图规划"><Empty text="等 static/map…" /></Card>;

  return (
    <div className="flex h-full min-h-0 flex-col">
      {!props.apiOk && (
        <div className="mb-1.5 text-note text-[color:var(--warn-fg)]">
          地图规划存在后端（runtime/map-plans）—— 先连后端；当前显示夹具底图
        </div>
      )}
      <div className="relative min-h-0 flex-1 overflow-hidden rounded border border-l1">
        <MapCanvas
          map={baseMap}
          /* 规划视图只画地图规划本身（槽位/点位/地形）—— 不叠加夹具的
             world/economy 帧：夹具是 bl 开局，出生点切到红方时基地/工人
             仍画在蓝方位置，用户会误读为「两个出生点基地一样」（实测踩过）。 */
          world={null}
          production={null}
          economy={null}
          catalog={catalog}
          layers={planningLayers}
          reserved={reserved}
          smooth={false}
          selection={null}
          onSelect={() => {}}
          marksOverride={proj.marks}
          slotsOverride={proj.slots}
          onBlankClick={st.placeMode ? place : () => st.patch({ selectedName: null })}
          draggableSlots={st.placeMode === null ? proj.slots : null}
          onSlotDrop={dropSlot}
          ghost={preview}
          onHover={setHoverPos}
          onMarkClick={(n) => st.patch({ selectedName: n })}
          onSlotClick={(n) => st.patch({ selectedName: n })}
          selectedName={st.selectedName}
        />
        <LayerPanel layers={planningLayers} onChange={setPlanningLayers}
                    map={baseMap} world={null}
                    extra={(
                      <div className="mt-1 border-t border-l1 pt-1 text-ghost">
                        可建区/矿区初始强制开 —— 编辑要在真实背景上放
                      </div>
                    )} />
        {st.placeMode && (
          <div className="pointer-events-none absolute bottom-8 left-2 rounded border border-[color:var(--warn-fg)] bg-panel px-2 py-1 text-note text-[color:var(--warn-fg)]">
            {st.placeMode === "mark"
              ? "放置点位：点击地图（吸附格心）· 再点按钮退出"
              : `放置槽位 ${st.slotSize}×${st.slotSize}（${st.slotKind}）：点击地图（吸附格心）· 悬停看绿/红预览`}
          </div>
        )}
        {slotError.current && (
          <div className="pointer-events-none absolute bottom-16 left-2 rounded border border-[color:var(--err-fg)] bg-panel px-2 py-1 text-note text-[color:var(--err-fg)]">
            {slotError.current}
          </div>
        )}
      </div>
    </div>
  );
}

/** 生产 tab：队列表格 + 试算投影。文件/成本/试算控制在左侧抽屉（QueueSidebar）。 */
function ProductionPlanning(props: { apiOk: boolean; initialPlanId?: string | null }) {
  const st = useQueueStore();
  const { catalog, map } = useFrames();

  useEffect(() => {
    void useQueueStore.getState().initOnce(props.apiOk, props.initialPlanId ?? null);
  }, [props.apiOk]);

  const options = useMemo(
    () => placementOptions(st.refPayload ?? map, st.refPayload ? st.refId : null),
    [st.refPayload, st.refId, map]);
  const zhOf = useMemo(() => {
    const m = new Map<string, string>();
    for (const e of catalog?.entries ?? []) m.set(e.stable_id, e.display_name_zh);
    return (id: string | null) => (id ? m.get(id) ?? id : "—");
  }, [catalog]);

  if (!props.apiOk) {
    return <Card title="生产规划">
      <Empty text="规划存在后端（python tools/serve_api.py，一个规划一个 YAML 文件）—— 先连后端" />
    </Card>;
  }
  if (!st.plans) return <Card title="生产规划"><Empty text="加载规划列表…" /></Card>;

  // 十九轮（用户拍板）：与复盘-生产同构的三区固定布局 ——
  // 上排 [队列编辑 2/3 | 文件与试算 1/3] 固定高度，下方试算投影占满（区内滚）。
  return (
    <div className="flex h-full flex-col gap-2 overflow-hidden">
      <div className="grid min-h-0 grid-cols-1 gap-2 xl:grid-cols-3 xl:h-[38%]">
        <Card title="生产规划"
              className="flex h-full min-h-0 flex-col overflow-hidden xl:col-span-2"
              right={<span className="text-note text-faint">
                {st.dirty ? "有未保存的修改（右上「文件与试算」里保存）" : "草稿与文件同步"}
              </span>}>
          <div className="min-h-0 flex-1 overflow-y-auto pr-1">
            <QueueTable items={st.items} update={st.update} setItems={st.setItems} options={options} />
            <div className="mt-1 text-note text-ghost">
              build 可选 placement（执行期才必需）；train / research / assign_workers 不需要
            </div>
          </div>
        </Card>
        <Card title="文件与试算"
              className="flex h-full min-h-0 flex-col overflow-hidden">
          <div className="min-h-0 flex-1 overflow-y-auto pr-1">
            <QueueSidebar />
          </div>
        </Card>
      </div>

      {/* 试算投影占满整宽（泳道向下抢高度）；「已保存对照」默认收起（用户拍板：平时不对比） */}
      <Card title={"试算投影" + (st.dirty && st.sim ? "（未保存草稿）" : "")}
            className="flex min-h-0 flex-1 flex-col overflow-hidden"
            right={st.baseSim ? (
              <button className="btn btn-ghost"
                      onClick={() => st.patch({ showBase: !st.showBase })}>
                {st.showBase ? "▾ 收起对照 —— 草稿占满整宽" : "▸ 对照已保存规划"}
              </button>
            ) : undefined}>
        {st.sim ? (
          <div className={st.showBase && st.baseSim
            ? "grid min-h-0 flex-1 grid-cols-1 gap-3 2xl:grid-cols-2"
            : "flex min-h-0 flex-1 flex-col"}>
            {/* 不传 initialDomain：板默认 zoomSpan 把初始窗口钳到 ZOOM_SPAN_MAX（10 分钟）。
                until_complete 后 horizon 可能是整局 30-60 分钟 —— 全塞一屏会「拖一下十几分钟」
                且事件密到泳道字不可读（二十六轮用户反馈）。曲线数据仍是完整的，拖动/跟随可看全程。 */}
            <ProjectionBoard frame={st.sim} catalog={catalog} zhOf={zhOf} />
            {st.showBase && st.baseSim && (
              <ProjectionBoard frame={st.baseSim} catalog={catalog} zhOf={zhOf} />
            )}
          </div>
        ) : (
          <div className="flex min-h-0 flex-1 items-center justify-center">
            <Empty text="点右上「试算」跑一版 —— 标准开局起步的真 planner 干跑，卡点即前瞻警报" />
          </div>
        )}
      </Card>
    </div>
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
  // I2/I4 可读名：step / reason / 组的中文都来自后端；谓词/运算符中文名来自 static/schema
  const vocab = vocabOf(schema);
  const stepZh = (id: string): string =>
    (graph.steps as { step_id: string; display_name_zh?: string }[])
      .find((s) => s.step_id === id)?.display_name_zh || id;

  return (
    <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
      <Card title={"策略图 · " + (graph.display_name_zh || graph.id) + " v" + graph.version}
            help={graph.description_zh || undefined}>
        <div className="space-y-1">
          {stepOrder.map((n: { step_id: string }) => (
            <button key={n.step_id} onClick={() => setStepId(n.step_id)}
                    title={n.step_id}
                    className={"w-full rounded border p-2 text-left transition-colors "
                      + (n.step_id === step?.step_id
                        ? "border-l2 bg-active"
                        : "border-l1 hover:bg-inset")}>
              <span className="font-medium text-strong">{stepZh(n.step_id)}</span>
              {n.step_id === graph.initial_step && <span className="ml-2 text-note text-faint">起点</span>}
              <span className="ml-2 text-note text-ghost">
                {graph.edges
                  .filter((e: { from: string }) => e.from === n.step_id)
                  .map((e: { to: string; reason: string }) => "→" + stepZh(e.to))
                  .join(" ")}
              </span>
            </button>
          ))}
        </div>
        <div className="mt-2 text-note text-ghost">
          转移上限 {Object.entries(graph.loop_limits).map(([k, v]) => k + "=" + v).join("、") || "—"} ·
          槽位 {graph.group_slots.map((s: string) => {
            const gid = graph.bindings[s] ?? "?";
            const gz = graph.group_names?.[gid];
            return s + "→" + (gz ? gz + "（" + gid + "）" : gid);
          }).join("、")}
        </div>
      </Card>
      <Card title={"分支 AST · " + stepZh(step?.step_id ?? "") + "（" + (step?.step_id ?? "—") + "）"}>
        {branches.length === 0 ? <Empty text="没有分支" /> : (
          <ol className="space-y-2">
            {branches.map((b) => (
              <li key={b.index} className="rounded border border-l1 p-2">
                <div className="flex gap-2 text-label">
                  <span className="text-faint">#{b.index}</span>
                  <span>{b.id ?? "（未命名）"}</span>
                  <span className="text-faint">{b.when === null ? "else" : "when"}</span>
                </div>
                {b.when !== null && <code className="mt-1 block text-blue-fg">{b.when}</code>}
                {b.actions.length > 0 && (
                  <ul className="mt-1 text-label text-dim">
                    {b.actions.map((a, i) => (
                      <li key={i} className={a.forbidden ? "text-ghost" : ""}>
                        → {a.text}
                        {a.forbidden && <span className="ml-1 text-[color:var(--warn-fg)]">（不可用：{a.forbidden}）</span>}
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
              <div key={k} className="text-label"
                   title={typeof (v as Record<string, unknown>)["description_zh"] === "string"
                     ? String((v as Record<string, unknown>)["description_zh"]) : undefined}>
                {k} : {String((v as Record<string, unknown>)["type"])}
                {" = "}{JSON.stringify((v as Record<string, unknown>)["default"])}
              </div>
            ))}
          </div>
          <div>
            <div className="text-dim">别名（definitions）</div>
            {Object.entries(graph.definitions).map(([k, v]) => (
              <div key={k} className="text-label">{k} = <code>{renderValue(v, 0, vocab)}</code></div>
            ))}
            {Object.keys(graph.definitions).length === 0 && <Empty text="无" />}
          </div>
        </div>
        <div className="mt-2 border-t border-l1 pt-2 text-note text-ghost">
          AST 编辑（改结构）按决策 U8 留到 next —— 现在先把「看与导出」做扎实；
          导出按钮把 static/strategy 原样存为 JSON。
        </div>
        <button
          className="btn btn-ghost mt-1"
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
