/**
 * 夹具装载（F0）—— 按需 fetch `public/fixtures/`，不打进 bundle。
 * F1 的 `JsonlFrameSource` 走同一条路（同样是"取一段 JSONL 文本"），组件不感知差别。
 */
export interface FixtureMeta {
  key: string;
  label: string;
  file: string;
  envelopes: number;
  from: number;
  to: number;
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
  const res = await fetch(BASE + "/" + meta.file);
  if (!res.ok) {
    throw new Error("夹具 " + meta.file + " 读不到（HTTP " + res.status + "）");
  }
  return await res.text();
}
