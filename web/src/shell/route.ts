/** 极简 hash 路由（不引 react-router：页面不多，URL 可分享即够） */
import { useEffect, useState } from "react";

/**
 * 页面清单（F13c 分组）：
 * - drive 驾驶：概览 / 地图 / 生产 / Flow（看现在）
 * - plan  规划：地图规划 / 生产规划 / Flow 装配（authoring —— drive 模式下按 R5 门控）
 * - diag  诊断：调试（帧检查暂并入调试页，不单造页面）
 * `initialTab`：规划三入口共用 PlanningPage，各自落到对应 tab。
 */
export const PAGES = [
  { key: "overview", label: "概览", icon: "▤", group: "drive" },
  { key: "map", label: "地图", icon: "◫", group: "drive" },
  { key: "production", label: "生产", icon: "▦", group: "drive" },
  { key: "flow", label: "Flow", icon: "◈", group: "drive" },
  { key: "plan-map", label: "地图规划", icon: "✥", group: "plan" },
  { key: "plan-production", label: "生产规划", icon: "✎", group: "plan" },
  { key: "plan-flow", label: "Flow 装配", icon: "❖", group: "plan" },
  { key: "debug", label: "调试", icon: "⚙", group: "diag" },
] as const;

export type PageKey = (typeof PAGES)[number]["key"];
export type PageGroup = "drive" | "plan" | "diag";

export const PAGE_GROUP_LABEL: Record<PageGroup, string> = {
  drive: "驾驶",
  plan: "规划",
  diag: "诊断",
};

/** 规划入口 → PlanningPage 的初始 tab（三入口同组件，见 App.tsx） */
export function planTabOf(key: PageKey): "map" | "production" | "flow" {
  if (key === "plan-map") return "map";
  if (key === "plan-flow") return "flow";
  return "production";
}

const isPage = (v: string): v is PageKey => PAGES.some((p) => p.key === v);

function current(): PageKey {
  const raw = window.location.hash.replace(/^#\/?/, "");
  // 旧链接 #/planning 落到生产规划（F9 时代的单入口）
  if (raw === "planning") return "plan-production";
  return isPage(raw) ? raw : "overview";
}

export function useRoute(): [PageKey, (p: PageKey) => void] {
  const [page, setPage] = useState<PageKey>(current);
  useEffect(() => {
    const onHash = (): void => setPage(current());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);
  const go = (p: PageKey): void => {
    window.location.hash = "#/" + p;
  };
  return [page, go];
}
