/**
 * 图层面板（可折叠小部件，2026-08-21 用户拍板）—— 图层开关不是常用功能，
 * 不占侧栏整张卡：收起时只是画布左下角一个小chip，展开才出现清单。
 *
 * 地图页（实时/复盘）与离线规划页共用（各自持有 layer state）。
 */
import { useState, type ReactNode } from "react";
import { LAYERS, type LayerKey, type LayerState } from "./layers";
import type { MapStatic, WorldFrame } from "../contract";
import { T } from "../shell/tokens";

export function LayerPanel(props: {
  layers: LayerState;
  onChange: (next: LayerState) => void;
  /** 可用性判定要吃的静态面/世界帧（规划页 world=null：依赖动态数据的层如实标不可用） */
  map: MapStatic;
  world?: WorldFrame | null;
  /** 附加行（如地图页的「位置插值」开关） */
  extra?: ReactNode;
}) {
  const [open, setOpen] = useState(false);
  const toggle = (k: LayerKey, v: boolean): void =>
    props.onChange({ ...props.layers, [k]: v });

  return (
    <div className="absolute bottom-2 left-2 z-10 flex max-w-64 flex-col gap-1">
      {open && (
        <div className={"rounded border border-neutral-700 bg-neutral-950/92 p-2 " + T.note}>
          <ul className="space-y-0.5">
            {LAYERS.map((l) => {
              const av = l.available(props.map, props.world ?? null);
              return (
                <li key={l.key}>
                  <label className={"flex items-center gap-2 " + (av.ok ? "" : "opacity-50")}
                         title={av.why ?? ""}>
                    <input
                      type="checkbox"
                      disabled={!av.ok}
                      checked={av.ok && props.layers[l.key]}
                      onChange={(e) => toggle(l.key, e.target.checked)}
                    />
                    <span>{l.label}</span>
                    {!av.ok && <span className="text-amber-500">不可用</span>}
                  </label>
                </li>
              );
            })}
          </ul>
          {props.extra}
        </div>
      )}
      <button
        onClick={() => setOpen((v) => !v)}
        className={"w-fit rounded border border-neutral-700 bg-neutral-950/85 px-2 py-0.5 "
          + T.note + " text-faint hover:text-neutral-200"}
        title="图层开关（不常用，收起来不占地方）"
      >
        图层 {open ? "▾" : "▴"}
      </button>
    </div>
  );
}
