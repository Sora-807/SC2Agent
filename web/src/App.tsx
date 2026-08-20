/**
 * 应用外壳（F1）
 *
 * 结构 = 会话条 / 时间线 / 左图标栏 / 主区 / 右对话栏（plan-frontend.md §5）。
 * App 本身不碰帧源实现，只用 store —— 换成 live 时这个文件零改动（决策 U1）。
 */
import { useEffect } from "react";
import { ChatDock } from "./shell/ChatDock";
import { IconRail } from "./shell/IconRail";
import { SessionBar } from "./shell/SessionBar";
import { Timeline } from "./shell/Timeline";
import { useRoute } from "./shell/route";
import { Overview } from "./pages/Overview";
import { MapPage } from "./pages/MapPage";
import { DebugPage } from "./pages/DebugPage";
import { ProductionPage } from "./pages/ProductionPage";
import { FlowPage, PlanningPage } from "./pages/Stubs";
import { useFrames } from "./store/frames";

export function App() {
  const [page, go] = useRoute();
  const { init, error, loading, fixtures } = useFrames();

  useEffect(() => {
    void init();
    return () => useFrames.getState().detach();
  }, [init]);

  if (error) {
    return (
      <div className="p-6 text-red-400">
        <h1 className="text-lg font-bold">帧源出错</h1>
        <pre className="mt-3 whitespace-pre-wrap text-sm">{error}</pre>
        <p className="mt-3 text-neutral-400">
          如果是"夹具清单读不到"，先在 web/ 下跑 <code>pnpm gen:fixtures</code>。
        </p>
      </div>
    );
  }

  return (
    <div className="flex min-h-screen flex-col p-3 text-sm">
      <SessionBar />
      <Timeline />
      <div className="flex min-h-0 flex-1">
        <IconRail page={page} go={go} />
        <main className="min-w-0 flex-1 overflow-auto px-3">
          {loading || fixtures.length === 0 ? (
            <div className="p-6 text-neutral-500">加载帧…</div>
          ) : (
            <>
              {page === "overview" && <Overview />}
              {page === "map" && <MapPage />}
              {page === "production" && <ProductionPage />}
              {page === "flow" && <FlowPage />}
              {page === "planning" && <PlanningPage />}
              {page === "debug" && <DebugPage />}
            </>
          )}
        </main>
        <ChatDock />
      </div>
      <footer className="mt-3 border-t border-neutral-800 pt-2 text-xs text-neutral-500">
        契约 ViewFrame v0.1（rev=1）· 唯一真相源 docs/plan-frontend.md §2 ·
        面板只读帧字段，未做任何本地派生（红线 C7）
      </footer>
    </div>
  );
}
