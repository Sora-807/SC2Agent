/**
 * 概览页（F6）—— 一屏看清"经济 / 战术 / 生产 / 风险"，每块可折叠、可跳到完整页。
 *
 * 布局按 ADR-0023 §2.1 的意思组织（上方生产+曲线、左下地图、右下 Flow），但**不做可拖拽 dock**
 * （决策 U9：dock 引擎 + 布局序列化是独立工程量，早期收益低）。折叠状态存 localStorage。
 */
import { type ReactNode } from "react";
import { ProjectionChart } from "../charts/ProjectionChart";
import { MapCanvas } from "../canvas/MapCanvas";
import { defaultLayers } from "../canvas/layers";
import { Empty, PAGE_SCROLL, fmtTime } from "../shell/ui";
import { AlertsList } from "../panels/AlertsList";
import { BootHint } from "../shell/BootHint";
import { useRoute, type PageKey } from "../shell/route";
import { useFrames } from "../store/frames";

/** 面板（二十轮起常开）：折叠按钮退役 —— 点掉只是藏内容不省空间（布局固定），
 *  没有真折叠的收益；要看的完整页跳转保留。 */
function Panel(props: {
  title: string;
  jump?: PageKey;
  right?: ReactNode;
  className?: string;
  children: ReactNode;
}) {
  const [, go] = useRoute();
  return (
    <section className={"rounded-lg border border-l1 bg-panel shadow-sm " + (props.className ?? "")}>
      <div className="flex items-baseline gap-2 px-3 py-2">
        <h2 className="font-semibold text-strong">{props.title}</h2>
        <div className="ml-auto flex items-center gap-2">
          {props.right}
          {props.jump && (
            <button className="btn btn-ghost"
                    onClick={() => go(props.jump!)}>完整页 →</button>
          )}
        </div>
      </div>
      <div className="px-3 pb-3">{props.children}</div>
    </section>
  );
}

export function Overview() {
  const { world, flow, production, projection, economy, alerts, map, catalog } = useFrames();
  const zhOf = (id: string | null): string => {
    if (!id) return "—";
    return catalog?.entries.find((e) => e.stable_id === id)?.display_name_zh ?? id;
  };
  const strategy = flow?.strategies.at(0) ?? null;

  return (
    <div className={PAGE_SCROLL + " grid grid-cols-1 gap-3 xl:grid-cols-3"}>
      {/* I6：真机首帧等待横幅 —— 无提示时不渲染任何节点，不影响网格布局 */}
      <BootHint className="xl:col-span-3" />
      <Panel title="经济" jump="production"
             right={<span className="text-note text-faint">
               {economy ? `领地 ${economy.domain_workers} 人 · 差量 ${economy.emitted_count}` : ""}
             </span>}>
        {world ? (
          <div className="space-y-1">
            <div className="flex gap-4 tabular-nums text-base">
              <span className="text-blue-fg">{world.economy.minerals}</span>
              <span className="text-[color:var(--ok-fg)]">{world.economy.vespene}</span>
              <span className={world.economy.supply_used >= world.economy.supply_cap
                ? "text-[color:var(--warn-fg)]" : "text-dim"}>
                {world.economy.supply_used}/{world.economy.supply_cap}
              </span>
            </div>
            {economy && (
              <div className="text-dim">
                {economy.tasks.map((t) => (
                  <span key={t.task} className="mr-3">
                    {t.task === "mineral" ? "采矿" : t.task === "gas" ? "采气" : "备用"}{" "}
                    <b className={t.actual < t.target ? "text-[color:var(--warn-fg)]" : ""}>
                      {t.actual}/{t.target}
                    </b>
                    {t.quota !== null && <span className="text-note">（维持 {t.quota}）</span>}
                  </span>
                ))}
              </div>
            )}
            <div className="text-note text-faint">可见单位 {world.units.length}</div>
          </div>
        ) : <Empty />}
      </Panel>

      <Panel title="Flow 状态" jump="flow">
        {strategy ? (
          <div className="space-y-1">
            <div><b>{strategy.strategy_ref}</b> · {strategy.active_step}
              {strategy.done && <span className="ml-2 text-[color:var(--ok-fg)]">已结束</span>}</div>
            <div className="text-dim">
              驻留 {strategy.step_elapsed.toFixed(1)}s · 第 {strategy.step_entry_count} 次进入 ·
              命中 {strategy.branch_hit?.branch_id ?? "无分支"}
            </div>
            <div className="text-dim">
              转移 {strategy.transition_count}/{strategy.transition_limit}
              {strategy.transitions.at(-1) && (
                <> · 最近 {strategy.transitions.at(-1)!.from} → {strategy.transitions.at(-1)!.to}
                  （{strategy.transitions.at(-1)!.reason}）</>
              )}
            </div>
            {strategy.exit_record && (
              <div className="text-[color:var(--ok-fg)]">
                退出 {strategy.exit_record.kind} / {strategy.exit_record.reason}
              </div>
            )}
            <div className="space-y-0.5 pt-1">
              {flow!.groups.map((g) => (
                <div key={g.group_id} className="text-dim">
                  {g.group_id}
                  {Object.entries(g.composition).map(([id, c]) => (
                    <span key={id} className="ml-2">{zhOf(id)} {c.current}/{c.target}</span>
                  ))}
                  <span className="ml-2 rounded bg-raised px-1.5 text-note">{g.refill_state}</span>
                </div>
              ))}
            </div>
          </div>
        ) : <Empty />}
      </Panel>

      <Panel title="风险" jump="debug"
             right={alerts && alerts.alerts.length > 0
               ? <span className="text-note text-[color:var(--warn-fg)]">{alerts.alerts.length} 条</span>
               : undefined}>
        {alerts
          ? <AlertsList alerts={alerts.alerts} empty={<Empty text="无警报" />} />
          : <Empty />}
      </Panel>

      <Panel title={projection?.source.kind === "live_queue"
               ? "投影 · 当前队列 " + projection.source.queue_name
               : "投影 · 参考计划"}
             jump="production" className="xl:col-span-3"
             right={<span className="text-note text-[color:var(--warn-fg)]">
               {projection?.source.kind === "draft" ? "队列为空，显示参考计划" : ""}
             </span>}>
        {projection ? <ProjectionChart frame={projection} height={150} /> : <Empty />}
      </Panel>

      <Panel title="地图" jump="map" className="xl:col-span-2">
        <div className="h-72">
          {map ? (
            <MapCanvas map={map} world={world} production={production}
                       economy={economy} catalog={catalog}
                       layers={defaultLayers()} smooth selection={null} onSelect={() => {}} />
          ) : <Empty />}
        </div>
      </Panel>

      <Panel title="生产队列" jump="production">
        {production && production.queues.length > 0 ? (
          production.queues.map((q) => (
            <div key={q.name} className="mb-2 last:mb-0">
              <div className="text-dim">
                {q.name} · 队首 <b className={q.head_status === "阻塞" ? "text-[color:var(--warn-fg)]" : ""}>
                  {q.head_status}</b>
                {q.blocked && <span className="ml-1 text-[color:var(--warn-fg)]">
                  {q.blocked.waited.toFixed(0)}s：{q.blocked.reason}</span>}
              </div>
              <ul className="mt-1 space-y-0.5">
                {q.items.slice(0, 6).map((it) => (
                  <li key={it.index} className={it.status === "队首阻塞" ? "text-[color:var(--warn-fg)]" : ""}>
                    {it.index}. {it.op} {it.op === "assign_workers" ? "维持 " + it.task : zhOf(it.stable_id)}
                    {it.count > 1 && " ×" + it.count}
                  </li>
                ))}
                {q.items.length > 6 && (
                  <li className="text-ghost">…还有 {q.items.length - 6} 项</li>
                )}
              </ul>
            </div>
          ))
        ) : <Empty />}
        {production && production.in_flight.length > 0 && (
          <div className="mt-1 border-t border-l1 pt-1 text-note text-dim">
            在途 {production.in_flight.map((f) => zhOf(f.stable_id)).join("、")}
          </div>
        )}
      </Panel>
    </div>
  );
}

export { fmtTime };