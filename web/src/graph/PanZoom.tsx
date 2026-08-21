/**
 * SVG 的平移/缩放视口（F12a）—— 策略图共用。
 *
 * 三条纪律（与 canvas/MapCanvas 同源）：
 * - G3：滚轮缩放走 **native 监听 + {passive:false}**（React onWheel 的 preventDefault 是空操作）；
 * - G2：视口状态只在内容尺寸变化（换图）时 fit；容器 resize 保住 scale 与中心；
 * - 拖动平移用 pointer capture。
 *
 * 不抽 canvas/useViewport.ts 共用：canvas 视口带「世界 y 向上」的翻转语义（canvas/view.ts），
 * SVG 是纯像素坐标系 —— 强行统一是伪共享。两边各自内聚，红线同守。
 */
import {
  useCallback, useEffect, useLayoutEffect, useRef, useState, type ReactNode,
} from "react";

export interface SvgViewport {
  scale: number;
  tx: number;
  ty: number;
}

export function PanZoom(props: {
  /** 内容尺寸（图坐标）；变化 = 换图 → 重新 fit（G2） */
  contentW: number;
  contentH: number;
  children: ReactNode;
  /** 双击居中请求：对象引用变化即触发（x/y 为内容坐标；scale 不动） */
  centerRequest?: { x: number; y: number } | null;
  /** 视口变化回调（页面拿它算屏幕 ↔ 图坐标，比如节点拖动） */
  onViewport?: (v: SvgViewport) => void;
}) {
  const hostRef = useRef<HTMLDivElement | null>(null);
  const [size, setSize] = useState({ w: 800, h: 500 });
  const [vp, setVp] = useState<SvgViewport>({ scale: 1, tx: 0, ty: 0 });
  const drag = useRef<{ x: number; y: number } | null>(null);
  const vpRef = useRef(vp);
  vpRef.current = vp;
  useEffect(() => props.onViewport?.(vp), [vp, props.onViewport]);

  // 容器尺寸
  useLayoutEffect(() => {
    const host = hostRef.current;
    if (!host) return;
    const ro = new ResizeObserver(() => {
      setSize({ w: Math.max(100, host.clientWidth), h: Math.max(100, host.clientHeight) });
    });
    ro.observe(host);
    setSize({ w: Math.max(100, host.clientWidth), h: Math.max(100, host.clientHeight) });
    return () => ro.disconnect();
  }, []);

  // fit：只在换图（内容尺寸变化）或首次尺寸就绪时做；resize 保住 scale（G2）
  const fitted = useRef<string | null>(null);
  useEffect(() => {
    const key = `${props.contentW}x${props.contentH}`;
    const needFit = fitted.current !== key;
    fitted.current = key;
    setVp((old) => {
      if (needFit) return fit(size.w, size.h, props.contentW, props.contentH);
      // resize：内容中心锚回容器中心
      const cx = old.tx + (props.contentW * old.scale) / 2;
      const cy = old.ty + (props.contentH * old.scale) / 2;
      return { ...old, tx: size.w / 2 - cx, ty: size.h / 2 - cy };
    });
  }, [size.w, size.h, props.contentW, props.contentH]);

  // 滚轮缩放：native + passive:false（G3），锚定光标
  useEffect(() => {
    const el = hostRef.current;
    if (!el) return;
    const handleWheel = (e: WheelEvent): void => {
      e.preventDefault();
      const r = el.getBoundingClientRect();
      const sx = e.clientX - r.left;
      const sy = e.clientY - r.top;
      const factor = e.deltaY < 0 ? 1.15 : 1 / 1.15;
      setVp((v) => zoomAtPoint(v, sx, sy, factor));
    };
    el.addEventListener("wheel", handleWheel, { passive: false });
    return () => el.removeEventListener("wheel", handleWheel);
  }, []);

  const doFit = useCallback(() => {
    setVp(fit(size.w, size.h, props.contentW, props.contentH));
  }, [size.w, size.h, props.contentW, props.contentH]);

  // 双击居中（外部用新对象引用触发）
  useEffect(() => {
    const req = props.centerRequest;
    if (!req) return;
    setVp((v) => ({ ...v, tx: size.w / 2 - req.x * v.scale, ty: size.h / 2 - req.y * v.scale }));
  }, [props.centerRequest, size.w, size.h]);

  return (
    <div className="relative">
      <div
        ref={hostRef}
        className="h-[52vh] min-h-[320px] cursor-grab overflow-hidden rounded border border-neutral-800 bg-neutral-950/60 active:cursor-grabbing"
        onPointerDown={(e) => {
          drag.current = { x: e.clientX, y: e.clientY };
          e.currentTarget.setPointerCapture(e.pointerId);
        }}
        onPointerMove={(e) => {
          if (!drag.current) return;
          const dx = e.clientX - drag.current.x;
          const dy = e.clientY - drag.current.y;
          drag.current = { x: e.clientX, y: e.clientY };
          setVp((v) => ({ ...v, tx: v.tx + dx, ty: v.ty + dy }));
        }}
        onPointerUp={(e) => {
          drag.current = null;
          e.currentTarget.releasePointerCapture(e.pointerId);
        }}
      >
        <svg width={size.w} height={size.h} className="block select-none">
          <g transform={`translate(${vp.tx},${vp.ty}) scale(${vp.scale})`}>
            {props.children}
          </g>
        </svg>
      </div>
      <div className="absolute right-2 top-2 flex gap-1">
        <button className="rounded border border-neutral-700 bg-neutral-900/80 px-2 py-0.5 text-note"
                onClick={doFit}>适应窗口</button>
        <button className="rounded border border-neutral-700 bg-neutral-900/80 px-2 py-0.5 text-note"
                onClick={() => setVp((v) => ({ ...v, scale: 1 }))}>1:1</button>
      </div>
      <div className="pointer-events-none absolute bottom-1 left-2 text-note text-ghost">
        滚轮缩放 · 拖动平移 · 双击节点居中 · {vp.scale.toFixed(2)}×
      </div>
    </div>
  );
}

function fit(cw: number, ch: number, contentW: number, contentH: number): SvgViewport {
  const scale = Math.min(1.6, Math.max(0.05,
    Math.min((cw - 24) / Math.max(1, contentW), (ch - 24) / Math.max(1, contentH))));
  return {
    scale,
    tx: (cw - contentW * scale) / 2,
    ty: (ch - contentH * scale) / 2,
  };
}

function zoomAtPoint(v: SvgViewport, sx: number, sy: number, factor: number): SvgViewport {
  const scale = Math.max(0.1, Math.min(6, v.scale * factor));
  // 锚定光标下的内容点：先算该点在内容坐标里的位置，再反解平移
  const cx = (sx - v.tx) / v.scale;
  const cy = (sy - v.ty) / v.scale;
  return { scale, tx: sx - cx * scale, ty: sy - cy * scale };
}
