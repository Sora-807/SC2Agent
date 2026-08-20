/** 极简 hash 路由（不引 react-router：页面就六个，URL 可分享即够） */
import { useEffect, useState } from "react";

export const PAGES = [
  { key: "overview", label: "概览", icon: "▤", stage: "F6" },
  { key: "map", label: "地图", icon: "◫", stage: "F2" },
  { key: "production", label: "生产", icon: "▦", stage: "F3" },
  { key: "flow", label: "Flow", icon: "◈", stage: "F4" },
  { key: "planning", label: "规划", icon: "✎", stage: "F9" },
  { key: "debug", label: "调试", icon: "⚙", stage: "F5" },
] as const;

export type PageKey = (typeof PAGES)[number]["key"];

const isPage = (v: string): v is PageKey => PAGES.some((p) => p.key === v);

function current(): PageKey {
  const raw = window.location.hash.replace(/^#\/?/, "");
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
