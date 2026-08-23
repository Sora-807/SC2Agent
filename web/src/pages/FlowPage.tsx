/**
 * Flow 状态图（F4/F12）—— 卡片节点 + branch 锚定边 + 可拖缩视口。
 *
 * 图来自 `static/strategy`（不变的结构），位置来自 `frame/flow`（每帧的状态）。
 * F12 之前的欠账（PLAN §1.3）：
 * - 图不能拖不能缩、节点会被推出容器拉不回（根因 K）→ graph/PanZoom；
 * - 有信息量的东西全在图下面的卡里、节点只有 step_id（根因 L）→ 卡片节点；
 * - branch 才是边，但边是匿名线讲不出「什么条件→去哪」（根因 M）→ 边锚在 branch 行；
 * - 布局 BFS 列不降交叉（根因 N）→ graph/layout Sugiyama-lite；
 * - 半宽补偿 transform（根因 O）→ 坐标一律左上角原点；
 * - strategies.at(0) 假设单实例（根因 P/C6）→ 实例选择器。
 *
 * live 下这页只读（R5）；节点拖动是图内坐标（不是面板 dock，U9 界线）。
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { branchExit, matchExitBranch, renderBranches, renderValue, storageKey, vocabOf } from "../graph/ast";
import { PanZoom, type SvgViewport } from "../graph/PanZoom";
import {
  BRANCH_ROW_H, GAP_X, HEADER_H, LANE_H, NODE_PAD_Y, NODE_W,
  backDip, edgeKey, layout, nodeHeight,
} from "../graph/layout";
import { Card, Empty, PAGE_SCROLL, fmtTime } from "../shell/ui";
import { useFrames } from "../store/frames";

/** 端口几何（用户反馈：端口要嵌在卡片圆角上，圆心压边框、一半在内一半在外，加粗增强视觉） */
const ENTRY_R = 5;     // 入口圆：圆心在卡片左上角点（0,0），骑在圆角上

export function FlowPage() {
  const { strategy: graph, flow, schema, catalog } = useFrames();
  // C6：不假设长度 1 —— 列表形状为多实例预留，选择器现在就有
  const strategies = flow?.strategies ?? [];
  const [instIdx, setInstIdx] = useState(0);
  const state = strategies[Math.min(instIdx, Math.max(0, strategies.length - 1))] ?? null;

  const [picked, setPicked] = useState<string | null>(null);
  const [centerReq, setCenterReq] = useState<{ x: number; y: number } | null>(null);
  const [overrides, setOverrides] = useState<Map<string, { x: number; y: number }>>(new Map());
  // 点 branch 行 → 下方详情卡看全文（行内两行截断后的「进一步显示途径」）
  const [branchPick, setBranchPick] = useState<{ step: string; idx: number } | null>(null);
  const vpRef = useRef<SvgViewport>({ scale: 1, tx: 0, ty: 0 });

  const laid = useMemo(
    () => graph
      ? layout(
          graph.steps.map((s: { step_id: string; branches: unknown[] }) => ({
            id: s.step_id, branchCount: Array.isArray(s.branches) ? s.branches.length : 0,
          })),
          graph.edges, graph.initial_step)
      : null,
    [graph],
  );

  // 换策略/换版本 → 重读持久化的节点位置（键带 version，重编译不继承旧坐标）
  const graphKey = graph ? storageKey(graph.id, graph.version) : null;
  useEffect(() => {
    if (!graphKey) return;
    try {
      const raw = localStorage.getItem(graphKey);
      const parsed = raw ? JSON.parse(raw) as Record<string, [number, number]> : null;
      setOverrides(
        parsed
          ? new Map(Object.entries(parsed).map(([id, [x, y]]) => [id, { x, y }]))
          : new Map(),
      );
    } catch {
      setOverrides(new Map()); // 坏数据不猜，回自动布局
    }
  }, [graphKey]);

  // 有效位置 = 自动布局 + 用户拖动覆盖；内容尺寸随之重算（PanZoom 换图即 fit）
  const nodes = useMemo(() => {
    const out: { id: string; x: number; y: number; h: number }[] = [];
    if (!graph || !laid) return out;
    for (const s of graph.steps as { step_id: string; branches: unknown[] }[]) {
      const auto = laid.positions.get(s.step_id) ?? { x: 0, y: 0 };
      const o = overrides.get(s.step_id);
      out.push({
        id: s.step_id,
        x: o?.x ?? auto.x,
        y: o?.y ?? auto.y,
        h: nodeHeight(Array.isArray(s.branches) ? s.branches.length : 0),
      });
    }
    return out;
  }, [graph, laid, overrides]);
  const posOf = useMemo(() => new Map(nodes.map((n) => [n.id, n])), [nodes]);
  const contentW = nodes.reduce((m, n) => Math.max(m, n.x + NODE_W), 0);
  const contentH = nodes.reduce((m, n) => Math.max(m, n.y + n.h), 0);

  // 进入次数（热度）：初始 1 次 + 转移历史里进入该节点的次数 —— 环在图上才读得出是环
  const entryCount = useMemo(() => {
    const m = new Map<string, number>();
    if (!graph) return m;
    m.set(graph.initial_step, 1);
    for (const t of state?.transitions ?? []) m.set(t.to, (m.get(t.to) ?? 0) + 1);
    return m;
  }, [graph, state]);

  const zhOf = (id: string): string =>
    catalog?.entries.find((e) => e.stable_id === id)?.display_name_zh ?? id;

  if (!graph || !laid) {
    return <Empty text="等待 static/strategy（该帧源没有策略图）" />;
  }

  // I2 可读名：step / reason / 组的中文都来自后端（static/strategy），没有就退回 identifier
  const stepMeta = new Map(
    (graph.steps as { step_id: string; display_name_zh?: string; description_zh?: string }[])
      .map((s) => [s.step_id, s]));
  const stepZh = (id: string): string => stepMeta.get(id)?.display_name_zh || id;
  const reasonZh = (r: string | null): string => (r == null ? "—" : graph.reasons?.[r] ?? r);
  const groupZh = (gid: string): string => graph.group_names?.[gid] ?? gid;
  const vocab = vocabOf(schema);

  const active = state?.active_step ?? null;
  const selected = picked ?? active;
  const lastT = state?.transitions.at(-1) ?? null;

  // 节点拖动：图坐标 = 原位 + 屏幕位移 / scale（PanZoom 的 vp 由 onViewport 持续同步）
  const nodeDrag = useRef<{
    id: string; sx: number; sy: number; ox: number; oy: number; moved: boolean;
    /** 最近一次 move 算出的图坐标（落盘读它，不读渲染闭包） */
    last?: { x: number; y: number };
  } | null>(null);
  /**
   * 落盘节点位置。**必须吃传进来的 map**，不能读渲染闭包里的 `overrides` ——
   * 快拖后立刻松手时，最后一次 pointermove 的 setState 可能还没提交重渲染，
   * 闭包里是上一帧的旧值，于是存下的位置比实际松手点少最后一小段，下次进来节点"回跳"。
   */
  const persist = (next: Map<string, { x: number; y: number }>): void => {
    if (!graphKey) return;
    const obj: Record<string, [number, number]> = {};
    for (const [id, p] of next) obj[id] = [p.x, p.y];
    try {
      localStorage.setItem(graphKey, JSON.stringify(obj));
    } catch {
      /* 存不进（隐私模式之类）就算了 —— 拖动只在内存里生效 */
    }
  };

  /** branch 行锚点：边从它对应的 branch 行右侧圆点出发（根因 M）；匹配不上退节点中心 */
  const branchAnchor = (stepId: string, edge: { kind: string; reason: string }): { x: number; y: number } | null => {
    const n = posOf.get(stepId);
    if (!n) return null;
    const step = (graph.steps as { step_id: string; branches: Record<string, unknown>[] }[])
      .find((s) => s.step_id === stepId);
    const idx = step ? matchExitBranch(step.branches ?? [], edge) : null;
    if (idx === null) return null;
    return { x: n.x + NODE_W, y: n.y + HEADER_H + NODE_PAD_Y + idx * BRANCH_ROW_H + BRANCH_ROW_H / 2 };
  };

  return (
    <div className={PAGE_SCROLL + " space-y-3"}>
      <Card
        title={`策略图 · ${graph.display_name_zh || graph.id} v${graph.version}`}
        help={
          (graph.description_zh ? graph.description_zh + "\n\n" : "")
          + "卡片 = 一步（头部是中文名）；行 = 分支（左侧条件、右侧去向）；"
          + "实心圆 = 出口（转场），方块 = 终局（结束整个策略）。"
          + "点行看全文，点选节点，拖动挪位，双击居中。"
        }
        right={
          <span className="flex items-center gap-3 text-note text-faint">
            {strategies.length > 1 && (
              <select
                className="rounded border border-l2 bg-panel px-1 py-0.5"
                value={instIdx}
                onChange={(e) => setInstIdx(Number(e.target.value))}
              >{strategies.map((s, i) => (
                <option key={s.instance_id} value={i}>{s.instance_id}</option>
              ))}</select>
            )}
            {state
              ? `转移 ${state.transition_count}/${state.transition_limit}`
              : "无运行状态"}
            {state?.done && <span className="text-[color:var(--ok-fg)]">已结束</span>}
            <button
              className="btn btn-ghost"
              title="清掉手动拖动的节点位置，回到自动布局"
              onClick={() => {
                setOverrides(new Map());
                if (graphKey) localStorage.removeItem(graphKey);
              }}
            >重新布局</button>
          </span>
        }
      >
        <PanZoom
          contentW={Math.max(1, contentW)}
          contentH={Math.max(1, contentH)}
          fitKey={graph.id + "@" + graph.version}
          centerRequest={centerReq}
          onViewport={(v) => { vpRef.current = v; }}
        >
          {/* 边：从 branch 行右侧圆点出（根因 M），统一汇入目标卡片左上角的空心入口点 */}
          {(graph.edges as { from: string; to: string; kind: string; reason: string }[]).map((e, i) => {
            const from = posOf.get(e.from);
            const to = posOf.get(e.to);
            if (!from || !to) return null;
            const isBack = laid.back.has(edgeKey(e.from, e.to));
            const anchor = branchAnchor(e.from, e);
            const sx = anchor?.x ?? from.x + NODE_W;
            const sy = anchor?.y ?? from.y + from.h / 2;
            const tx = to.x;
            const ty = to.y;
            const isLast = lastT?.from === e.from && lastT?.to === e.to;
            const walked = state?.transitions.some((t) => t.from === e.from && t.to === e.to);
            const color = isLast ? "#fbbf24" : walked ? "#34d399" : "#4b5563";
            let d: string;
            if (isBack) {
              // 回边：保留车道 —— 沉到两端点中较低者下方 + laneIndex × LANE_H
              const lane = laid.lanes.get(edgeKey(e.from, e.to)) ?? 0;
              const dip = backDip(from.y + from.h, to.y + to.h, lane);
              d = `M ${sx} ${sy} C ${sx + GAP_X * 0.7} ${dip}, ${tx - GAP_X * 0.7} ${dip}, ${tx} ${ty}`;
            } else {
              d = `M ${sx} ${sy} L ${tx} ${ty}`;
            }
            return (
              <g key={i}>
                <path d={d} fill="none" stroke={color}
                      strokeWidth={isLast ? 2 : 1.2}
                      strokeDasharray={isBack ? "4 3" : undefined} />
                <title>{`${stepZh(e.from)}（${e.from}）→ ${stepZh(e.to)}（${e.to}）｜${e.kind}/${e.reason}`}</title>
                <text x={(sx + tx) / 2} y={isBack ? Math.max(from.y + from.h, to.y + to.h) + LANE_H : (sy + ty) / 2 - 4}
                      textAnchor="middle" fontSize={10} fill={color}>{reasonZh(e.reason)}</text>
              </g>
            );
          })}

          {/* 卡片节点：头部（step_id/热度/elapsed）+ 主体（一行一个 branch） */}
          {nodes.map((n) => {
            const step = (graph.steps as { step_id: string; branches: Record<string, unknown>[] }[])
              .find((s) => s.step_id === n.id);
            const branches = step ? renderBranches(step.branches ?? [], schema) : [];
            const isActive = n.id === active;
            const entered = entryCount.get(n.id) ?? 0;
            const hitIdx = state?.branch_hit?.step_id === n.id ? state.branch_hit.index : null;
            // 该 branch 行的**出口三态**：转场（去某 step）/ 终局（策略结束）/ 留在本步。
            // 之前只判"有没有边"，于是"终局"和"继续等待"共用一句「留在本步」——
            // 策略图里最重要的终态语义被画成了等待（详情卡里才有真相）。
            const outcomeOf = (idx: number): { text: string; tone: "step" | "end" | "stay" } => {
              const ex = branchExit(step?.branches?.[idx]);
              if (ex.kind === "end") {
                return { text: "✕ 结束策略 · " + reasonZh(ex.reason), tone: "end" };
              }
              if (ex.kind === "step") {
                const e = (graph.edges as { from: string; to: string; kind: string; reason: string }[])
                  .find((ed) => ed.from === n.id && matchExitBranch(step?.branches ?? [], ed) === idx);
                // 声明了 exit_step 却找不到边 = 数据异常，如实说，不假装"留在本步"
                return e
                  ? { text: stepZh(e.to) + "（" + reasonZh(e.reason) + "）", tone: "step" }
                  : { text: "（缺边）" + ex.reason, tone: "step" };
              }
              return { text: "留在本步", tone: "stay" };
            };
            return (
              <g key={n.id}
                 transform={`translate(${n.x},${n.y})`}
                 className="cursor-pointer"
                 onPointerDown={(e) => {                   e.stopPropagation();     // 不触发画布平移
                   const host = (e.currentTarget.closest("svg")?.parentElement)?.getBoundingClientRect();
                   nodeDrag.current = {
                     id: n.id,
                     sx: host ? e.clientX - host.left : e.clientX,
                     sy: host ? e.clientY - host.top : e.clientY,
                     ox: n.x, oy: n.y, moved: false,
                   };
                   (e.target as Element).setPointerCapture?.(e.pointerId);
                 }}
                 onPointerMove={(e) => {
                   const dr = nodeDrag.current;
                   if (!dr || dr.id !== n.id) return;
                   const host = (e.currentTarget.closest("svg")?.parentElement)?.getBoundingClientRect();
                   const cx = host ? e.clientX - host.left : e.clientX;
                   const cy = host ? e.clientY - host.top : e.clientY;
                   const dx = (cx - dr.sx) / vpRef.current.scale;
                   const dy = (cy - dr.sy) / vpRef.current.scale;
                   if (Math.abs(dx) + Math.abs(dy) > 2) dr.moved = true;
                   const at = { x: dr.ox + dx, y: dr.oy + dy };
                   dr.last = at;            // 落盘读它，不读渲染闭包（否则丢最后一段位移）
                   setOverrides((m) => new Map(m).set(n.id, at));
                 }}
                 onPointerUp={(e) => {
                   const dr = nodeDrag.current;
                   nodeDrag.current = null;
                   (e.target as Element).releasePointerCapture?.(e.pointerId);
                   if (dr && !dr.moved) {
                     setPicked(n.id);       // 点击（没拖）= 选中
                   } else if (dr) {
                     // 用 dr.last 合并出最终 map 再落盘 —— setState 提交与否都不影响结果
                     setOverrides((m) => {
                       const next = new Map(m);
                       if (dr.last) next.set(dr.id, dr.last);
                       persist(next);
                       return next;
                     });
                   }
                 }}
                 onDoubleClick={() => setCenterReq({ x: n.x + NODE_W / 2, y: n.y + n.h / 2 })}
              >
                <title>
                  {`${stepZh(n.id)}（${n.id}）`
                   + (stepMeta.get(n.id)?.description_zh
                      ? "｜" + stepMeta.get(n.id)!.description_zh : "")}
                </title>
                <rect width={NODE_W} height={n.h} rx={6}
                      fill={isActive ? "rgba(52,211,153,0.14)" : "rgba(38,38,38,0.6)"}
                      stroke={n.id === selected ? "#e5e7eb" : isActive ? "#34d399" : "#525252"}
                      strokeWidth={isActive ? 2 : 1} />
                {/* 头部两行：左=step 中文名（identifier 进 tooltip）；右=活跃点+耗时+进入次数（互不重叠） */}
                <text x={8} y={16} fontSize={12} fontWeight={600}
                      fill={isActive ? "#d1fae5" : "#d4d4d4"}
                      data-sid={n.id}>{stepZh(n.id)}</text>
                {n.id === graph.initial_step && (
                  <text x={8} y={27} fontSize={9} fill="#737373">△ 起点</text>
                )}
                {entered > 1 && (
                  <text x={NODE_W - 8} y={27} textAnchor="end" fontSize={9} fill="#a3a3a3">进入 ×{entered}</text>
                )}
                {isActive && (
                  <>
                    <circle cx={NODE_W - 12} cy={12} r={4} fill="#34d399">
                      <animate attributeName="opacity" values="1;0.25;1" dur="1.4s" repeatCount="indefinite" />
                    </circle>
                    <text x={NODE_W - 22} y={16} textAnchor="end" fontSize={9} fill="#6ee7b7">
                      {state?.step_elapsed.toFixed(1)}s
                    </text>
                  </>
                )}
                {/* 入口点：骑在卡片左上角圆弧上（圆心压角点、一半在内一半在外、加粗） */}
                <circle cx={0} cy={0} r={ENTRY_R} fill="#0d1117" stroke="#c3c9d4" strokeWidth={2} />
                {/* 主体：一行一个 branch（左右分栏可换行）；行间分隔线；出边行右端画出口点；
                    点行 → 下方详情卡看全文（截断不再死路） */}
                {branches.map((b, idx) => {
                  const y = HEADER_H + NODE_PAD_Y + idx * BRANCH_ROW_H;
                  const hit = hitIdx === b.index;
                  const oc = outcomeOf(b.index);
                  const hasEdge = oc.tone === "step";
                  return (
                    <g key={b.index} className="cursor-pointer"
                       onClick={(ev) => {
                         ev.stopPropagation();
                         setBranchPick(branchPick?.step === n.id && branchPick.idx === b.index
                           ? null
                           : { step: n.id, idx: b.index });
                       }}>
                      {hit && (
                        <rect x={2} y={y} width={NODE_W - 4} height={BRANCH_ROW_H}
                              fill="rgba(52,211,153,0.16)" />
                      )}
                      {idx < branches.length - 1 && (
                        <line x1={4} y1={y + BRANCH_ROW_H} x2={NODE_W - 4} y2={y + BRANCH_ROW_H}
                              stroke="rgba(148,163,184,0.14)" strokeWidth={1} />
                      )}
                      {hasEdge && (
                        <circle cx={NODE_W} cy={y + BRANCH_ROW_H / 2} r={4}
                                fill={hit ? "#fbbf24" : "#8a8f98"} stroke="#0d1117" strokeWidth={1.5} />
                      )}
                      {/* 终局用**方块**终结符，与转场的出口圆点形状不同 ——
                          「结束整个策略」和「转到下一步」是两件事，不能只靠文字区分 */}
                      {oc.tone === "end" && (
                        <rect x={NODE_W - 3.5} y={y + BRANCH_ROW_H / 2 - 3.5} width={7} height={7}
                              fill={hit ? "#fca5a5" : "#f87171"} stroke="#0d1117" strokeWidth={1.5} />
                      )}
                      <foreignObject x={4} y={y} width={NODE_W - 8} height={BRANCH_ROW_H}>
                        <div
                             title={`${b.when ?? "else（无条件）"} → ${oc.text}`}
                             className={"flex h-full items-center gap-1 "
                               + (hit ? "text-[color:var(--ok-fg)]" : "")}>
                          <span className={"min-w-0 flex-1 break-all "
                            + (hit ? "text-[color:var(--ok-fg)]" : "text-blue-fg")}
                                style={{ display: "-webkit-box", WebkitLineClamp: 2,
                                         WebkitBoxOrient: "vertical", overflow: "hidden",
                                         fontSize: "10px", lineHeight: "12px" }}>
                            {b.nameZh ? b.nameZh + "｜" : ""}{b.when ?? "else"}
                          </span>
                          <span className={"shrink-0 whitespace-nowrap "
                            + (hit
                              ? "text-[color:var(--warn-fg)]"
                              : oc.tone === "end"
                                ? "text-[color:var(--err-fg)]"
                                : oc.tone === "stay"
                                  ? "text-faint"
                                  : "text-dim")}
                                style={{ fontSize: "10px", lineHeight: "12px" }}>
                            {oc.tone === "end" ? oc.text : "→ " + oc.text}
                          </span>
                        </div>
                      </foreignObject>
                    </g>
                  );
                })}
              </g>
            );
          })}
        </PanZoom>
        <div className="mt-1 flex flex-wrap gap-3 text-note text-ghost">
          <span><i className="mr-1 inline-block h-0.5 w-4 bg-raised align-middle" />未走过</span>
          <span><i className="mr-1 inline-block h-0.5 w-4 bg-[color:var(--ok-fg)] align-middle" />走过</span>
          <span><i className="mr-1 inline-block h-0.5 w-4 bg-[color:var(--warn-fg)] align-middle" />最近一次</span>
          <span>虚线 = 回边（成环）· 空心圆 = 入口 · 实心圆 = 出口 · 点行看全文 · 点选节点 · 拖动挪位 · 双击居中</span>
        </div>
      </Card>

      {/* 分支详情：卡片内两行截断后的「看全文」出口（用户反馈：截断后没有进一步显示途径） */}
      {branchPick && (() => {
        const step = (graph.steps as { step_id: string; branches: Record<string, unknown>[] }[])
          .find((s) => s.step_id === branchPick.step);
        const rows = step ? renderBranches(step.branches ?? [], schema) : [];
        const b = rows.find((r) => r.index === branchPick.idx);
        if (!b) return null;
        return (
          <Card title={`分支详情 · ${stepZh(branchPick.step)}（${branchPick.step}）#${b.index}${b.id ? "（" + b.id + "）" : ""}${b.nameZh ? " · " + b.nameZh : ""}`}
                right={
                  <button className="btn btn-ghost"
                          onClick={() => setBranchPick(null)}>关闭</button>
                }>
            <div className="space-y-1">
              <div>
                <span className="mr-2 text-faint">条件</span>
                <code className="text-blue-fg break-all">{b.when ?? "else（无条件，只能放最后）"}</code>
              </div>
              <div>
                <span className="mr-2 text-faint">动作</span>
                {b.actions.length === 0
                  ? <span className="text-faint">（无 —— 只等待条件）</span>
                  : (
                    <ul className="ml-16 space-y-0.5 text-dim">
                      {b.actions.map((a, i) => (
                        <li key={i} className={a.forbidden ? "text-ghost" : ""}>
                          → {a.text}
                          {a.forbidden && <span className="ml-1 text-note text-[color:var(--warn-fg)]">（不可用：{a.forbidden}）</span>}
                        </li>
                      ))}
                    </ul>
                  )}
              </div>
            </div>
          </Card>
        );
      })()}

      <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
        <Card title="转移历史"
              help="策略实际走过的步与切换原因；最新一次高亮。名称后的括号是 reason 标识符。">
          {state && state.transitions.length > 0 ? (
            <ol className="space-y-0.5">
              {[...state.transitions].reverse().map((t, i) => (
                <li key={i} className={i === 0 ? "text-[color:var(--warn-fg)]" : "text-dim"}
                    title={`${t.from} → ${t.to}｜${t.kind}/${t.reason}`}>
                  {fmtTime(t.at)} {stepZh(t.from)} → {stepZh(t.to)}
                  <span className="ml-1 text-note text-ghost">{reasonZh(t.reason)}</span>
                </li>
              ))}
            </ol>
          ) : <Empty text="还没有转移" />}
          {state && (
            <div className="mt-2">
              <div className="h-1 rounded bg-raised">
                <div className="h-1 rounded bg-[color:var(--accent-blue-fg)]"
                     style={{ width: Math.min(100, (state.transition_count / Math.max(1, state.transition_limit)) * 100) + "%" }} />
              </div>
              <div className="mt-0.5 text-note text-ghost">
                转移上限是**兜底**不是出口（ADR-0021 §4）：每个环必须有自己的 exit
              </div>
            </div>
          )}
          {state?.exit_record && (
            <div className="mt-2 rounded border border-[color:var(--ok-fg)] bg-[color:var(--ok-bg)] p-2">
              策略已结束：{state.exit_record.kind} / {reasonZh(String(state.exit_record.reason ?? ""))}
            </div>
          )}
        </Card>

        <Card title="绑定与参数"
              help={"槽位 = 策略里的角色名，绑定到装配的组（如 main → 步兵组 G_INF）；"
                    + "每个组列兵种的 当前/目标 人数与补员状态；参数是本局生效值（声明说明悬停可见）。"}>
          <div className="space-y-1">
            <div className="text-dim">
              槽位 {graph.group_slots.map(
                (s: string) => {
                  const gid = graph.bindings[s] ?? "?";
                  return `${s} → ${groupZh(gid)}${gid !== groupZh(gid) ? "（" + gid + "）" : ""}`;
                }).join("、")}
            </div>
            {flow?.groups.map((g) => (
              <div key={g.group_id} className="text-dim" title={`group_id: ${g.group_id}`}>
                {groupZh(g.group_id)}
                {Object.entries(g.composition).map(([id, c]) => (
                  <span key={id} className="ml-2"
                        title={`当前人数 / 装配目标（${id}）`}>
                    {zhOf(id)} {c.current}/{c.target}
                  </span>
                ))}
                <span className="ml-2 rounded bg-raised px-1.5 text-note"
                      title="补员状态：满足 / 滞回区 / 补兵中 / 已截断（后端按滞回语义判定）">
                  {g.refill_state}
                </span>
              </div>
            ))}
            <div className="border-t border-l1 pt-1 text-dim">
              {Object.entries(state?.params ?? {}).map(([k, v]) => {
                const desc = typeof (graph.params[k]?.["description_zh"]) === "string"
                  ? String(graph.params[k]["description_zh"]) : "";
                return (
                  <span key={k} className="mr-3" title={desc || undefined}>
                    {k}={JSON.stringify(v)}
                  </span>
                );
              })}
            </div>
            {Object.keys(graph.definitions).length > 0 && (
              <div className="text-note text-faint">
                别名：{Object.entries(graph.definitions).map(([k, v]) => (
                  <div key={k} className="ml-2">
                    <span className="text-dim">{k}</span> = <code>{renderValue(v, 0, vocab)}</code>
                  </div>
                ))}
              </div>
            )}
          </div>
        </Card>
      </div>
    </div>
  );
}
