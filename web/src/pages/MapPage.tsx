/**
 * 地图页（F2）—— 全屏 canvas + 图层开关 + 选中检查器。
 *
 * 所有面板只读 store 的帧字段；帧里没有的层置灰并显示**后端为什么没给**（红线 C7）。
 */
import { useMemo, useState } from "react";
import { MapCanvas, type Selection } from "../canvas/MapCanvas";
import { LayerPanel } from "../canvas/LayerPanel";
import { defaultLayers } from "../canvas/layers";
import { BootHint } from "../shell/BootHint";
import { Card, Empty, fmtTime } from "../shell/ui";
import { useFrames } from "../store/frames";

export function MapPage() {
  const { map, world, production, economy, catalog, flow } = useFrames();
  const [layers, setLayers] = useState(defaultLayers);
  const [smooth, setSmooth] = useState(true);
  const [selection, setSelection] = useState<Selection | null>(null);

  const zhOf = useMemo(() => {
    const m = new Map<string, string>();
    for (const e of catalog?.entries ?? []) m.set(e.stable_id, e.display_name_zh);
    return (id: string) => m.get(id) ?? id;
  }, [catalog]);

  if (!map) {
    return (
      <div className="flex h-full min-h-0 flex-col gap-2">
        {/* I6：真机首帧要 1-2 分钟，空窗期明说「正在连」而不是让用户猜是不是没同步 */}
        <BootHint />
        <Empty text="等待 static/map…" />
      </div>
    );
  }

  const selected = selection ? world?.units.find((u) => u.tag === selection.tag) ?? null : null;

  return (
    <div className="flex h-full min-h-0 flex-col gap-2">
      <BootHint />
      <div className="flex min-h-0 flex-1 gap-3">
        <div className="relative min-h-0 min-w-0 flex-1 overflow-hidden">
          <MapCanvas
            map={map}
            world={world}
            production={production}
            economy={economy}
            catalog={catalog}
            layers={layers}
            smooth={smooth}
            selection={selection}
            onSelect={setSelection}
          />
          {/* 图层开关收进画布左下角折叠小部件（用户拍板：不常用，不占侧栏整张卡） */}
          <LayerPanel
            layers={layers}
            onChange={setLayers}
            map={map}
            world={world}
            extra={(
              <label className="mt-1 flex items-center gap-2 border-t border-neutral-800 pt-1">
                <input type="checkbox" checked={smooth} onChange={(e) => setSmooth(e.target.checked)} />
                <span>位置插值</span>
              </label>
            )}
          />
        </div>

        <div className="w-72 min-h-0 shrink-0 space-y-3 overflow-y-auto pb-6">


          <Card title="地图">
            <ul className="space-y-0.5 text-neutral-300">
              <li>{map.map_name} · {map.size[0]}×{map.size[1]} · 出生 {map.spawn}</li>
              <li>大区 {map.regions.big.length} / 叶区 {map.regions.leaf.length}</li>
              <li>槽位 {map.build_slots.length} · 标记 {map.pos_marks.length} · 资源 {map.resource_nodes.length}</li>
              <li className={map.terrain ? "" : "text-amber-400"}>
                地形 {map.terrain ? "已下发" : "null（需后端 B4；现在是纯色底，不伪造）"}
              </li>
            </ul>
          </Card>

          <Card title="选中">
            {selected ? (
              <ul className="space-y-0.5 text-neutral-300">
                <li className="font-medium">{zhOf(selected.stable_id)}{selected.form ? `（${selected.form}）` : ""}</li>
                <li className="text-faint">{selected.stable_id} · tag {selected.tag}</li>
                <li>归属 {selected.owner}{selected.group_id ? ` · 组 ${selected.group_id}` : ""}</li>
                <li>坐标 {selected.pos[0].toFixed(1)}, {selected.pos[1].toFixed(1)}</li>
                <li>HP {Math.round(selected.hp)}/{Math.round(selected.hp_max)}
                  {selected.shield > 0 ? ` · 护盾 ${Math.round(selected.shield)}` : ""}</li>
                {selected.build_progress < 1 && (
                  <li className="text-amber-400">在建 {(selected.build_progress * 100).toFixed(0)}%</li>
                )}
                {selected.footprint && (
                  <li className="text-faint">
                    footprint {selected.footprint.tl.join(",")} → {selected.footprint.br.join(",")}
                    <span className="ml-1 text-note">（后端按 ADR-0027 算好）</span>
                  </li>
                )}
                {selected.producing && selected.producing.length > 0 && (
                  <li>在训 {selected.producing.map((p) => zhOf(p.stable_id)).join("、")}</li>
                )}
                {selected.addon && <li>挂件 {selected.addon}</li>}
                {selected.order && (
                  <li className="text-dim">
                    命令 {selected.order.atom ?? "（未映射）"}
                    <span className="ml-1 text-note text-ghost">
                      raw {selected.order.ability_raw}
                    </span>
                  </li>
                )}
                {selected.buffs.length > 0 && <li>buff {selected.buffs.join("、")}</li>}
              </ul>
            ) : (
              <Empty text="点地图上的单位查看详情" />
            )}
          </Card>

          <Card title="分组">
            {flow && flow.groups.length > 0 ? (
              <ul className="space-y-1">
                {flow.groups.map((g) => (
                  <li key={g.group_id}>
                    <span className="font-medium">{g.group_id}</span>
                    <span className="ml-2 rounded bg-neutral-800 px-1.5 text-note">{g.refill_state}</span>
                    <div className="text-dim">
                      {Object.entries(g.composition).map(([id, c]) => (
                        <span key={id} className="mr-2">
                          {zhOf(id)} {c.current}/{c.target}
                        </span>
                      ))}
                    </div>
                    <div className="text-note text-ghost">
                      {g.center ? `组心 ${g.center[0].toFixed(1)}, ${g.center[1].toFixed(1)}` : "组心 —"}
                      {g.hp_ratio !== null ? ` · HP ${(g.hp_ratio * 100).toFixed(0)}%` : ""}
                    </div>
                  </li>
                ))}
              </ul>
            ) : <Empty text="本帧没有分组" />}
          </Card>

          {production && production.in_flight.length > 0 && (
            <Card title="在途建造">
              <ul className="space-y-1 text-neutral-300">
                {production.in_flight.map((f, i) => (
                  <li key={i}>
                    {zhOf(f.stable_id)}
                    <span className="ml-1 text-note text-faint">
                      {f.kind} · 等待 {f.frames_waited} 帧
                      {f.retries > 0 ? ` · 重试 ${f.retries}` : ""}
                      {f.builder_tag === null ? " · 待重试" : ""}
                    </span>
                    {f.attempted_slots.length > 0 && (
                      <div className="text-note text-ghost">
                        已试槽位 {f.attempted_slots.join("、")}
                      </div>
                    )}
                  </li>
                ))}
              </ul>
              <div className="mt-1 text-note text-ghost">
                开启「摆放调试」图层可在地图上看到期望落点与已试槽位
              </div>
            </Card>
          )}
        </div>
      </div>
    </div>
  );
}

export { fmtTime };
