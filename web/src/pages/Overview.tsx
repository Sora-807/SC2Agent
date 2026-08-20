/**
 * 概览页（F1 先把已有面板搬过来；F6 再做折叠/跳转/布局）
 * 面板全部只读 store 的帧字段，零本地派生（红线 C7）。
 */
import { ProjectionChart } from "../charts/ProjectionChart";
import { useFrames } from "../store/frames";
import { Card, Empty, sevClass } from "../shell/ui";

export function Overview() {
  const { world, flow, production, projection, alerts } = useFrames();

  return (
    <div className="grid grid-cols-1 gap-3 lg:grid-cols-3">
      <Card title="经济">
        {world ? (
          <dl className="grid grid-cols-2 gap-1">
            <Row k="晶体矿" v={world.economy.minerals} />
            <Row k="高能瓦斯" v={world.economy.vespene} />
            <Row k="供给" v={world.economy.supply_used + " / " + world.economy.supply_cap} />
            <Row k="可见单位" v={world.units.length} />
            <Row k="敌方聚类" v={world.enemy_clusters ? world.enemy_clusters.length : "后端未实现"} />
          </dl>
        ) : <Empty />}
      </Card>

      <Card title="Flow 状态">
        {flow ? (
          <>
            {flow.strategies.map((s) => (
              <div key={s.instance_id} className="space-y-1">
                <div className="font-medium">{s.strategy_ref} · {s.active_step}</div>
                <div className="text-neutral-400">
                  驻留 {s.step_elapsed.toFixed(1)}s · 第 {s.step_entry_count} 次进入 ·
                  命中分支 {s.branch_hit?.branch_id ?? "—"}
                </div>
                <div className="text-neutral-400">
                  转移 {s.transition_count}/{s.transition_limit}
                  {s.transitions.length > 0 && (
                    <> · 最近 {s.transitions.at(-1)?.from} → {s.transitions.at(-1)?.to}
                      （{s.transitions.at(-1)?.reason}）</>
                  )}
                </div>
              </div>
            ))}
            <div className="mt-2 space-y-1">
              {flow.groups.map((g) => (
                <div key={g.group_id} className="text-neutral-300">
                  {g.group_id}
                  {Object.entries(g.composition).map(([id, c]) => (
                    <span key={id} className="ml-2">{id} {c.current}/{c.target}</span>
                  ))}
                  <span className="ml-2 rounded bg-neutral-800 px-1.5 text-xs">{g.refill_state}</span>
                </div>
              ))}
            </div>
          </>
        ) : <Empty />}
      </Card>

      <Card title="警报">
        {alerts && alerts.alerts.length > 0 ? (
          <ul className="space-y-1">
            {alerts.alerts.map((a) => (
              <li key={a.id}>
                <span className={sevClass(a.severity)}>[{a.severity}]</span> {a.text_zh}
              </li>
            ))}
          </ul>
        ) : <Empty text="无警报" />}
      </Card>

      <Card title="投影（planner）· 晶体矿 / 高能瓦斯" className="lg:col-span-3">
        {projection ? <ProjectionChart frame={projection} /> : <Empty />}
      </Card>

      <Card title="生产队列" className="lg:col-span-3">
        {production ? production.queues.map((q) => (
          <div key={q.name}>
            <div className="mb-1 text-neutral-400">队列 {q.name} · 队首 <b>{q.head_status}</b></div>
            <table className="w-full text-left">
              <thead className="text-neutral-500">
                <tr><th>#</th><th>op</th><th>类型</th><th>数量</th><th>状态</th><th>阻塞原因</th></tr>
              </thead>
              <tbody>
                {q.items.map((it) => (
                  <tr key={it.index} className={it.status === "队首阻塞" ? "text-amber-400" : ""}>
                    <td>{it.index}</td><td>{it.op}</td>
                    <td>{it.stable_id ?? (it.task ?? "—")}</td>
                    <td>{it.count}</td><td>{it.status}</td>
                    <td className="text-neutral-400">{it.block_reason ?? ""}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {production.dropped.length > 0 && (
              <div className="mt-2 text-red-400">
                掉项：{production.dropped.map((d) => d.op + " " + (d.stable_id ?? "") + "（" + d.reason + "）").join("；")}
              </div>
            )}
          </div>
        )) : <Empty />}
      </Card>
    </div>
  );
}

const Row = (p: { k: string; v: string | number }) => (
  <>
    <dt className="text-neutral-500">{p.k}</dt>
    <dd>{p.v}</dd>
  </>
);
