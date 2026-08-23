/**
 * uPlot 的 React 薄封装（F3 的图表基元，F0 先给投影卡用）
 *
 * 约定：`options` 必须由调用方 useMemo 稳定住（否则每次 render 会重建图）。
 * 需要随数据变化的东西（如事件竖线）走 ref，不要塞进 options。
 *
 * F15 新增三个可选受控面（互不依赖）：
 * - `xRange`：受控 x 轴定域。变化（含 data 变化后）调 u.setScale —— 投影板靠它
 *   实现「滚轮调宽度 + 中心跟顶层时间轴」；不传则保持 uPlot 自适应。
 * - `onCursor`/`onPick`：宿主 div 上的 native 监听（clientX → posToVal），
 *   给投影板做泳道/曲线双向游标联动与点击检查。
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
  /** 受控 x 轴定域（投影板用）；不传 = uPlot 自适应 */
  xRange?: { from: number; to: number } | null;
  /** 鼠标在绘图区横移（t = 光标处游戏时间）；离开绘图区回调 null */
  onCursor?: (t: number | null) => void;
  /** 点击绘图区（t = 点击处游戏时间） */
  onPick?: (t: number) => void;
}) {
  const hostRef = useRef<HTMLDivElement | null>(null);
  const plotRef = useRef<uPlot | null>(null);
  const dataRef = useRef<AlignedData>(props.data);
  dataRef.current = props.data;

  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;
    // 宽度取**内容盒**：clientWidth 含 padding，uPlot 按它画会溢出内容区 8px，
    // 表现为图表框底下多一条横向滚动条（2026-08-22 用户实测报的 bug）
    const contentWidth = (el: HTMLElement): number => {
      const cs = getComputedStyle(el);
      return el.clientWidth - parseFloat(cs.paddingLeft) - parseFloat(cs.paddingRight);
    };
    const u = new uPlot(
      { ...props.options, width: Math.max(160, contentWidth(host)), height: props.height },
      dataRef.current,
      host,
    );
    plotRef.current = u;

    const ro = new ResizeObserver(() => {
      const w = contentWidth(host);
      if (w > 0) u.setSize({ width: w, height: props.height });
    });
    ro.observe(host);

    return () => {
      ro.disconnect();
      u.destroy();
      plotRef.current = null;
    };
  }, [props.options, props.height]);

  // data / 受控定域：setData 之后 uPlot 会重算自适应刻度 → 受控时必须把 scale 压回去
  useEffect(() => {
    const u = plotRef.current;
    if (!u) return;
    u.setData(props.data);
    const r = props.xRange;
    if (r && r.to > r.from) u.setScale("x", { min: r.from, max: r.to });
  }, [props.data, props.xRange?.from, props.xRange?.to]);

  // 光标/点击：native 监听 + clientX → posToVal（canvas 与宿主同宽同原点）
  useEffect(() => {
    const host = hostRef.current;
    if (!host || (!props.onCursor && !props.onPick)) return;
    const tAt = (clientX: number): number | null => {
      const u = plotRef.current;
      if (!u) return null;
      const hostX = clientX - host.getBoundingClientRect().left;
      const t = u.posToVal(hostX, "x");
      return Number.isFinite(t) ? t : null;
    };
    const move = (e: MouseEvent): void => {
      const t = tAt(e.clientX);
      if (t !== null) props.onCursor?.(t);
    };
    const leave = (): void => props.onCursor?.(null);
    const click = (e: MouseEvent): void => {
      const t = tAt(e.clientX);
      if (t !== null) props.onPick?.(t);
    };
    host.addEventListener("mousemove", move);
    host.addEventListener("mouseleave", leave);
    host.addEventListener("click", click);
    return () => {
      host.removeEventListener("mousemove", move);
      host.removeEventListener("mouseleave", leave);
      host.removeEventListener("click", click);
    };
  }, [props.onCursor, props.onPick]);

  // 双主题轮：图表默认坐在「凹陷面板」上 —— 与卡片面板拉开底色层次
  // （用户反馈：框里内容纯白缺区分，投影曲线等要有底色）
  return <div ref={hostRef}
    className={"overflow-hidden rounded-md border border-l1 bg-inset px-1 " + (props.className ?? "")} />;
}
