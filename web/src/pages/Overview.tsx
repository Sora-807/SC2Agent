/**
 * 概览页（F6）—— 一屏看清"经济 / 战术 / 生产 / 风险"，每块可折叠、可跳到完整页。
 *
 * 布局按 ADR-0023 §2.1 的意思组织（上方生产+曲线、左下地图、右下 Flow），但**不做可拖拽 dock**
 * （决策 U9：dock 引擎 + 布局序列化是独立工程量，早期收益低）。折叠状态存 localStorage。
 */
import { useEffect, useState, type ReactNode } from "react";
import { ProjectionChart } from "../charts/ProjectionChart";
import { MapCanvas } from "../canvas/MapCanvas";
import { defaultLayers } from "../canvas/layers";
import { Empty, PAGE_SCROLL, fmtTime, sevClass } from "../shell/ui";
import { useRoute, type PageKey } from "../shell/route";
import { useFrames } from "../store/frames";

const STORE_KEY = "sc2agent.overview.collapsed";

function useCollapsed(): [Set<string>, (k: string) => void] {
  const [set, setSet] = useState<Set<string>>(() => {
    try {
      return new Set<string>(JSON.parse(localStorage.getItem(STORE_KEY) ?? "[]") as string[]);
    } catch {
      return new Set();
    }
  });
  useEffect(() => {
    localStorage.setItem(STORE_KEY, JSON.stringify([...set]));
  }, [set]);
  return [set, (k) => setSet((s) => {
    const next = new Set(s);
    if (next.has(k)) next.delete(k);
    else next.add(k);
    return next;
  })];
}

function Panel(props: {
  id: string;
  title: string;
  jump?: PageKey;
  right?: ReactNode;
  className?: string;
  collapsed: Set<string>;
  toggle: (k: string) => void;
  children: ReactNode;
}) {
  const [, go] = useRoute();
  const off = props.collapsed.has(props.id);
  return (
    <section className={"rounded border border-neutral-800 bg-neutral-900/40 " + (props.className ?? "")}>
      <div className="flex items-baseline gap-2 px-3 py-2">
        <button className="text-neutral-500 hover:text-neutral-300"
                onClick={() => props.toggle(props.id)} title={off ? "展开" : "折叠"}>
          {off ? "▸" : "▾"}
        </button>
        <h2 className="font-semibold text-neutral-200">{props.title}</h2>
        <div className="ml-auto flex items-center gap-2">
          {props.right}
          {props.jump && (
            <button className="rounded border border-neutral-800 px-1.5 text-[11px] text-neutral-500 hover:text-neutral-300"
                    onClick={() => go(props.jump!)}>完整页 →</button>
          )}
        </div>
      </div>
      {!off && <div className="px-3 pb-3">{props.children}</div>}
    </section>
  );
}

export function Overview() {
  const { world, flow, production, projection, economy, alerts, map, catalog } = useFrames();
  const [collapsed, toggle] = useCollapsed();
  const zhOf = (id: string | null): string => {
    if (!id) return "—";
    return catalog?.entries.find((e) => e.stable_id === id)?.display_name_zh ?? id;
  };
  const strategy = flow?.strategies.at(0) ?? null;
  const shared = { collapsed, toggle };

  return (
    <div className={PAGE_SCROLL + " grid grid-cols-1 gap-3 xl:grid-cols-3"}>
      <Panel id="econ" title="经济" {...shared} jump="production"
             right={<span className="text-[11px] text-neutral-500">
               {economy ? `领地 ${economy.domain_workers} 人 · 差量 ${economy.emitted_count}` : ""}
             </span>}>
        {world ? (
          <div className="space-y-1">
            <div className="flex gap-4 text-base">
              <span className="text-sky-300">{world.economy.minerals}</span>
              <span className="text-emerald-300">{world.economy.vespene}</span>
              <span className={world.economy.supply_used >= world.economy.supply_cap
                ? "text-amber-400" : "text-neutral-300"}>
                {world.economy.supply_used}/{world.economy.supply_cap}
              </span>
            </div>
            {economy && (
              <div className="text-neutral-400">
                {economy.tasks.map((t) => (
                  <span key={t.task} className="mr-3">
                    {t.task === "mineral" ? "采矿" : t.task === "gas" ? "采气" : "备用"}{" "}
                    <b className={t.actual < t.target ? "text-amber-400" : ""}>
                      {t.actual}/{t.target}
                    </b>
                    {t.quota !== null && <span className="text-[10px]">（维持 {t.quota}）</span>}
                  </span>
                ))}
              </div>
            )}
            <div className="text-[11px] text-neutral-500">可见单位 {world.units.length}</div>
          </div>
        ) : <Empty />}
      </Panel>

      <Panel id="flow" title="Flow 状态" {...shared} jump="flow">
        {strategy ? (
          <div className="space-y-1">
            <div><b>{strategy.strategy_ref}</b> · {strategy.active_step}
              {strategy.done && <span className="ml-2 text-emerald-400">已结束</span>}</div>
            <div className="text-neutral-400">
              驻留 {strategy.step_elapsed.toFixed(1)}s · 第 {strategy.step_entry_count} 次进入 ·
              命中 {strategy.branch_hit?.branch_id ?? "无分支"}
            </div>
            <div className="text-neutral-400">
              转移 {strategy.transition_count}/{strategy.transition_limit}
              {strategy.transitions.at(-1) && (
                <> · 最近 {strategy.transitions.at(-1)!.from} → {strategy.transitions.at(-1)!.to}
                  （{strategy.transitions.at(-1)!.reason}）</>
              )}
            </div>
            {strategy.exit_record && (
              <div className="text-emerald-400">
                退出 {strategy.exit_record.kind} / {strategy.exit_record.reason}
              </div>
            )}
            <div className="space-y-0.5 pt-1">
              {flow!.groups.map((g) => (
                <div key={g.group_id} className="text-neutral-300">
                  {g.group_id}
                  {Object.entries(g.composition).map(([id, c]) => (
                    <span key={id} className="ml-2">{zhOf(id)} {c.current}/{c.target}</span>
                  ))}
                  <span className="ml-2 rounded bg-neutral-800 px-1.5 text-[10px]">{g.refill_state}</span>
                </div>
              ))}
            </div>
          </div>
        ) : <Empty />}
      </Panel>

      <Panel id="alerts" title="风险" {...shared} jump="debug"
             right={alerts && alerts.alerts.length > 0
               ? <span className="text-[11px] text-amber-400">{alerts.alerts.length} 条</span>
               : undefined}>
        {alerts && alerts.alerts.length > 0 ? (
          <ul className="space-y-1">
            {alerts.alerts.map((a) => (
              <li key={a.id + a.at}>
                <span className={sevClass(a.severity)}>●</span> {a.text_zh}
              </li>
            ))}
          </ul>
        ) : <Empty text="无警报" />}
      </Panel>

      <Panel id="proj"
             title={projection?.source.kind === "live_queue"
               ? "投影 · 当前队列 " + projection.source.queue_name
               : "投影 · 参考计划"}
             {...shared} jump="production" className="xl:col-span-3"
             right={<span className="text-[11px] text-amber-500">
               {projection?.source.kind === "draft" ? "队列为空，显示参考计划" : ""}
             </span>}>
        {projection ? <ProjectionChart frame={projection} height={150} /> : <Empty />}
      </Panel>

      <Panel id="map" title="地图" {...shared} jump="map" className="xl:col-span-2">
        <div className="h-72">
          {map ? (
            <MapCanvas map={map} world={world} production={production}
                       layers={defaultLayers()} smooth selection={null} onSelect={() => {}} />
          ) : <Empty />}
        </div>
      </Panel>

      <Panel id="queue" title="生产队列" {...shared} jump="production">
        {production && production.queues.length > 0 ? (
          production.queues.map((q) => (
            <div key={q.name} className="mb-2 last:mb-0">
              <div className="text-neutral-400">
                {q.name} · 队首 <b className={q.head_status === "阻塞" ? "text-amber-400" : ""}>
                  {q.head_status}</b>
                {q.blocked && <span className="ml-1 text-amber-400">
                  {q.blocked.waited.toFixed(0)}s：{q.blocked.reason}</span>}
              </div>
              <ul className="mt-1 space-y-0.5">
                {q.items.slice(0, 6).map((it) => (
                  <li key={it.index} className={it.status === "队首阻塞" ? "text-amber-400" : ""}>
                    {it.index}. {it.op} {it.op === "assign_workers" ? "维持 " + it.task : zhOf(it.stable_id)}
                    {it.count > 1 && " ×" + it.count}
                  </li>
                ))}
                {q.items.length > 6 && (
                  <li className="text-neutral-600">…还有 {q.items.length - 6} 项</li>
                )}
              </ul>
            </div>
          ))
        ) : <Empty />}
        {production && production.in_flight.length > 0 && (
          <div className="mt-1 border-t border-neutral-800 pt-1 text-[11px] text-neutral-400">
            在途 {production.in_flight.map((f) => zhOf(f.stable_id)).join("、")}
          </div>
        )}
      </Panel>
    </div>
  );
}

export { fmtTime };