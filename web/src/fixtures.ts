/**
 * 夹具装载（F0）—— 按需 fetch `public/fixtures/`，不打进 bundle。
 * F1 的 `JsonlFrameSource` 走同一条路（同样是"取一段 JSONL 文本"），组件不感知差别。
 *
 * 二十六轮起多了第二类复盘源：**对局录像**（后端 live 帧流落盘的 JSONL）。
 * 与夹具同格式，只是从 API 取（`/api/recordings/<id>/jsonl`）而不是 vite 静态目录。
 */
export interface FixtureMeta {
  key: string;
  label: string;
  file: string;
  envelopes: number;
  from: number;
  to: number;
  /** 快照锚点（ADR-0024 §6）：时间线上可跳的点。录制时写进 index.json */
  snapshots?: number[];
  /** 完整 URL（录像走后端 API；夹具不填 = 拼 /fixtures/<file>） */
  url?: string;
}

const BASE = "/fixtures";

export async function listFixtures(): Promise<FixtureMeta[]> {
  const res = await fetch(BASE + "/index.json");
  if (!res.ok) {
    throw new Error("夹具清单读不到（HTTP " + res.status + "）：先跑 pnpm gen:fixtures");
  }
  return (await res.json()) as FixtureMeta[];
}

export async function loadFixture(meta: FixtureMeta): Promise<string> {
  const res = await fetch(meta.url ?? `${BASE}/${meta.file}`);
  if (!res.ok) {
    throw new Error("帧源 " + (meta.url ?? meta.file) + " 读不到（HTTP " + res.status + "）");
  }
  return await res.text();
}

/** 对局录像清单（二十六轮）：后端没起 / 没录像 / 不支持 = 空表，不算错误。
 *  二十七轮用户拍板的形态：一条「对局记录 08-23 14:30 · 人族 vs 神族 · LadderMap（12:34）」——
 *  时间、什么族打什么族、什么地图，别的一个字都不多。 */
export async function listRecordings(apiBase: string): Promise<FixtureMeta[]> {
  try {
    const res = await fetch(`${apiBase}/api/recordings`);
    if (!res.ok) return [];
    const rows = (await res.json()) as {
      id: string; label?: string; driver?: string; map?: string;
      envelopes?: number; to?: number; state?: string;
      started_at?: string; my_race_zh?: string; enemy_race_zh?: string;
    }[];
    return rows.map((r) => {
      const mm = Math.floor((r.to ?? 0) / 60);
      const ss = Math.round((r.to ?? 0) % 60);
      const when = (r.started_at ?? "").replace("T", " ").slice(5, 16);
      const races = r.my_race_zh || r.enemy_race_zh
        ? ` · ${r.my_race_zh ?? "—"} vs ${r.enemy_race_zh ?? "—"}`
        : "";
      const map = r.map ? ` · ${r.map}` : "";
      return {
        key: "rec:" + r.id,
        label: `对局记录 ${when}${races}${map}（${mm}:${String(ss).padStart(2, "0")}`
          + `${r.state === "recording" ? " · 录制中" : ""}）`,
        file: r.id,
        url: `${apiBase}/api/recordings/${r.id}/jsonl`,
        envelopes: r.envelopes ?? 0,
        from: 0,
        to: r.to ?? 0,
      };
    });
  } catch {
    return [];
  }
}
