/**
 * Flow 状态图（F4）—— 策略图 + 当前位置 + 转移原因。
 *
 * 图来自 `static/strategy`（不变的结构），位置来自 `frame/flow`（每帧的状态）——
 * 两者合起来才画得出"当前在哪个节点、从哪条边过来的、还有哪些没走过"。
 * 只靠转移历史推图会看不见没走过的节点。
 *
 * 用手写 SVG 而不是 React Flow + ELK：策略图是 2~5 个节点的小图，
 * 那两个依赖加起来 ~1.4MB，为这点规模不值（原计划的选型在知道图规模后修正）。
 * live 下这页**只读**（R5：live 不能编辑模块与 Strategy）。
 */
import { useMemo, useState } from "react";
import { layout, renderBranches, renderValue } from "../graph/ast";
import { Card, Empty, PAGE_SCROLL, fmtTime } from "../shell/ui";
import { useFrames } from "../store/frames";

const COL_W = 190;
const ROW_H = 96;
const NODE_W = 132;
const NODE_H = 52;

export function FlowPage() {
  const { strategy: graph, flow, schema, catalog } = useFrames();
  const state = flow?.strategies.at(0) ?? null;
  const [picked, setPicked] = useState<string | null>(null);

  const laid = useMemo(
    () => (graph ? layout(graph.steps, graph.edges, graph.initial_step) : null),
    [graph],
  );
  const zhOf = (id: string): string =>
    catalog?.entries.find((e) => e.stable_id === id)?.display_name_zh ?? id;

  if (!graph || !laid) {
    return <Empty text="等待 static/strategy（该帧源没有策略图）" />;
  }

  const active = state?.active_step ?? null;
  const selected = picked ?? active;
  const step = graph.steps.find((s: { step_id: string }) => s.step_id === selected) ?? null;
  const branches = step ? renderBranches(step.branches, schema) : [];
  const pos = new Map(laid.nodes.map((n) => [n.id, n]));
  const width = laid.cols * COL_W + 40;
  const height = laid.rows * ROW_H + 40;

  const nodeCenter = (id: string): [number, number] => {
    const n = pos.get(id);
    if (!n) return [0, 0];
    return [20 + n.col * COL_W + NODE_W / 2, 20 + n.row * ROW_H + NODE_H / 2];
  };

  return (
    <div className={PAGE_SCROLL + " space-y-3"}>
      <Card
        title={`策略图 · ${graph.id} v${graph.version}`}
        right={
          <span className="text-note text-faint">
            {state
              ? `转移 ${state.transition_count}/${state.transition_limit}`
              : "无运行状态"}
            {state?.done && <span className="ml-2 text-emerald-400">已结束</span>}
          </span>
        }
      >
        <div className="overflow-auto">
          <svg width={width} height={height} className="min-w-full">
            {/* 边 */}
            {laid.edges.map((e, i) => {
              const [x1, y1] = nodeCenter(e.from);
              const [x2, y2] = nodeCenter(e.to);
              const isLast =
                state?.transitions.at(-1)?.from === e.from &&
                state?.transitions.at(-1)?.to === e.to;
              const walked = state?.transitions.some((t) => t.from === e.from && t.to === e.to);
              const color = isLast ? "#fbbf24" : walked ? "#34d399" : "#4b5563";
              // 回边画成下方绕行的弧，避免和正向边重叠
              const d = e.back
                ? `M ${x1} ${y1 + NODE_H / 2} C ${x1} ${y1 + 70}, ${x2} ${y2 + 70}, ${x2} ${y2 + NODE_H / 2}`
                : `M ${x1 + NODE_W / 2} ${y1} L ${x2 - NODE_W / 2} ${y2}`;
              const mx = (x1 + x2) / 2;
              const my = e.back ? Math.max(y1, y2) + 52 : (y1 + y2) / 2 - 6;
              return (
                <g key={i}>
                  <path d={d} fill="none" stroke={color} strokeWidth={isLast ? 2 : 1.2}
                        strokeDasharray={e.back ? "4 3" : undefined} />
                  <title>{`${e.from} → ${e.to}｜${e.kind}/${e.reason}`}</title>
                  <text x={mx} y={my} textAnchor="middle" fontSize={10} fill={color}>
                    {e.reason}
                  </text>
                </g>
              );
            })}
            {/* 节点 */}
            {laid.nodes.map((n) => {
              const isActive = n.id === active;
              const entered = isActive ? state?.step_entry_count ?? 1 : null;
              const x = 20 + n.col * COL_W;
              const y = 20 + n.row * ROW_H;
              return (
                <g key={n.id} onClick={() => setPicked(n.id)} className="cursor-pointer">
                  <rect
                    x={x - NODE_W / 2 + NODE_W / 2} y={y} rx={8} width={NODE_W} height={NODE_H}
                    transform={`translate(${-NODE_W / 2},0)`}
                    fill={isActive ? "rgba(52,211,153,0.14)" : "rgba(38,38,38,0.6)"}
                    stroke={n.id === selected ? "#e5e7eb" : isActive ? "#34d399" : "#525252"}
                    strokeWidth={isActive ? 2 : 1}
                  />
                  <text x={x} y={y + 20} textAnchor="middle" fontSize={12}
                        fill={isActive ? "#d1fae5" : "#d4d4d4"}>{n.id}</text>
                  {n.id === graph.initial_step && (
                    <text x={x} y={y + 34} textAnchor="middle" fontSize={9} fill="#737373">起点</text>
                  )}
                  {isActive && (
                    <>
                      <text x={x} y={y + 34} textAnchor="middle" fontSize={9} fill="#6ee7b7">
                        第 {entered} 次 · {state?.step_elapsed.toFixed(1)}s
                      </text>
                      <circle cx={x + NODE_W / 2 - 10} cy={y + 10} r={4} fill="#34d399">
                        <animate attributeName="opacity" values="1;0.25;1" dur="1.4s"
                                 repeatCount="indefinite" />
                      </circle>
                    </>
                  )}
                </g>
              );
            })}
          </svg>
        </div>
        <div className="mt-1 flex flex-wrap gap-3 text-note text-ghost">
          <span><i className="mr-1 inline-block h-0.5 w-4 bg-neutral-600 align-middle" />未走过</span>
          <span><i className="mr-1 inline-block h-0.5 w-4 bg-emerald-400 align-middle" />走过</span>
          <span><i className="mr-1 inline-block h-0.5 w-4 bg-amber-400 align-middle" />最近一次</span>
          <span>虚线 = 回边（成环）· 边上的字是退出原因 · 点节点看分支</span>
        </div>
      </Card>

      <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
        <Card title={`分支 · ${selected ?? "—"}`} right={
          state?.branch_hit?.step_id === selected
            ? <span className="text-note text-emerald-400">
                本帧命中 {state.branch_hit.branch_id ?? "#" + state.branch_hit.index}
              </span>
            : undefined
        }>
          {branches.length === 0 ? <Empty text="该 step 没有分支" /> : (
            <ol className="space-y-2">
              {branches.map((b) => {
                const hit = state?.branch_hit?.step_id === selected &&
                  state.branch_hit.index === b.index;
                return (
                  <li key={b.index}
                      className={"rounded border p-2 " + (hit
                        ? "border-emerald-700 bg-emerald-950/30"
                        : "border-neutral-800")}>
                    <div className="flex items-baseline gap-2">
                      <span className="text-faint">#{b.index}</span>
                      <span className="font-medium">{b.id ?? "（未命名）"}</span>
                      {hit && <span className="text-note text-emerald-400">← 本帧命中</span>}
                    </div>
                    <div className="mt-1 text-neutral-300">
                      {b.when === null
                        ? <span className="text-faint">else（无条件，只能放最后）</span>
                        : <code className="text-sky-300">{b.when}</code>}
                    </div>
                    {b.actions.length > 0 && (
                      <ul className="mt-1 space-y-0.5">
                        {b.actions.map((a, i) => (
                          <li key={i} className={a.forbidden ? "text-ghost" : "text-neutral-300"}>
                            → {a.text}
                            {a.forbidden && (
                              <span className="ml-1 text-note text-amber-600">
                                （不可用：{a.forbidden}）
                              </span>
                            )}
                          </li>
                        ))}
                      </ul>
                    )}
                  </li>
                );
              })}
            </ol>
          )}
        </Card>

        <div className="space-y-3">
          <Card title="转移历史">
            {state && state.transitions.length > 0 ? (
              <ol className="space-y-0.5">
                {[...state.transitions].reverse().map((t, i) => (
                  <li key={i} className={i === 0 ? "text-amber-300" : "text-dim"}>
                    {fmtTime(t.at)} {t.from} → {t.to}
                    <span className="ml-1 text-note text-ghost">
                      {t.kind}/{t.reason}
                    </span>
                  </li>
                ))}
              </ol>
            ) : <Empty text="还没有转移" />}
            {state && (
              <div className="mt-2">
                <div className="h-1 rounded bg-neutral-800">
                  <div className="h-1 rounded bg-sky-500"
                       style={{ width: Math.min(100, (state.transition_count / Math.max(1, state.transition_limit)) * 100) + "%" }} />
                </div>
                <div className="mt-0.5 text-note text-ghost">
                  转移上限是**兜底**不是出口（ADR-0021 §4）：每个环必须有自己的 exit
                </div>
              </div>
            )}
            {state?.exit_record && (
              <div className="mt-2 rounded border border-emerald-800 bg-emerald-950/30 p-2">
                策略已结束：{state.exit_record.kind} / {state.exit_record.reason}
              </div>
            )}
          </Card>

          <Card title="绑定与参数">
            <div className="space-y-1">
              <div className="text-dim">
                槽位 {graph.group_slots.map((s: string) => `${s}→${graph.bindings[s] ?? "?"}`).join("、")}
              </div>
              {flow?.groups.map((g) => (
                <div key={g.group_id} className="text-neutral-300">
                  {g.group_id}
                  {Object.entries(g.composition).map(([id, c]) => (
                    <span key={id} className="ml-2">{zhOf(id)} {c.current}/{c.target}</span>
                  ))}
                  <span className="ml-2 rounded bg-neutral-800 px-1.5 text-note">{g.refill_state}</span>
                </div>
              ))}
              <div className="border-t border-neutral-800 pt-1 text-dim">
                {Object.entries(state?.params ?? {}).map(([k, v]) => (
                  <span key={k} className="mr-3">{k}={JSON.stringify(v)}</span>
                ))}
              </div>
              {Object.keys(graph.definitions).length > 0 && (
                <div className="text-note text-faint">
                  别名：{Object.entries(graph.definitions).map(([k, v]) => (
                    <div key={k} className="ml-2">
                      <span className="text-dim">{k}</span> = <code>{renderValue(v)}</code>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </Card>
        </div>
      </div>
    </div>
  );
}
