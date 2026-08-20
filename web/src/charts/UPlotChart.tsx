/**
 * uPlot 的 React 薄封装（F3 的图表基元，F0 先给投影卡用）
 *
 * 约定：`options` 必须由调用方 useMemo 稳定住（否则每次 render 会重建图）。
 * 需要随数据变化的东西（如事件竖线）走 ref，不要塞进 options。
 */
import { useEffect, useRef } from "react";
import uPlot from "uplot";
import type { AlignedData, Options } from "uplot";
import "uplot/dist/uPlot.min.css";

export type ChartOptions = Omit<Options, "width" | "height">;

export function UPlotChart(props: {
  data: AlignedData;
  options: ChartOptions;
  height: number;
  className?: string;
}) {
  const hostRef = useRef<HTMLDivElement | null>(null);
  const plotRef = useRef<uPlot | null>(null);
  const dataRef = useRef<AlignedData>(props.data);
  dataRef.current = props.data;

  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;
    const u = new uPlot(
      { ...props.options, width: Math.max(160, host.clientWidth), height: props.height },
      dataRef.current,
      host,
    );
    plotRef.current = u;

    const ro = new ResizeObserver(() => {
      const w = host.clientWidth;
      if (w > 0) u.setSize({ width: w, height: props.height });
    });
    ro.observe(host);

    return () => {
      ro.disconnect();
      u.destroy();
      plotRef.current = null;
    };
  }, [props.options, props.height]);

  useEffect(() => {
    plotRef.current?.setData(props.data);
  }, [props.data]);

  return <div ref={hostRef} className={props.className} />;
}
