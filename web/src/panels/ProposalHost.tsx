/**
 * 提案审批的宿主（F7）：解决"帧里有 vs 后端有"的取数问题。
 *
 * 帧里的提案（live 会推）是权威；但用户可能在看**本地夹具**而提案在后端 ——
 * 那时就从 `GET /api/proposals` 取。两条路都走同一个审批组件。
 */
import { useEffect, useState } from "react";
import { listProposals } from "../api/proposals";
import { Empty, PAGE_SCROLL } from "../shell/ui";
import { ProposalReview } from "./ProposalReview";
import type { Proposal } from "../contract";

export function ProposalHost(props: {
  id: string;
  fromFrame: Proposal | null;
  onClose: () => void;
}) {
  const [fetched, setFetched] = useState<Proposal | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [nonce, setNonce] = useState(0);

  useEffect(() => {
    if (props.fromFrame) return;
    let alive = true;
    listProposals()
      .then((rows) => alive && setFetched(rows.find((p) => p.id === props.id) ?? null))
      .catch((e: Error) => alive && setErr(e.message));
    return () => { alive = false; };
  }, [props.id, props.fromFrame, nonce]);

  const p = props.fromFrame ?? fetched;

  return (
    <div className={PAGE_SCROLL + " space-y-2"}>
      <button className="text-note text-faint hover:text-neutral-300"
              onClick={props.onClose}>← 返回</button>
      {err && <div className="text-red-400">取提案失败：{err}</div>}
      {p ? (
        <ProposalReview
          proposal={p}
          onDone={() => {
            setNonce((n) => n + 1);      // 重取一次，让状态/决定立刻反映出来
            if (props.fromFrame) props.onClose();
          }}
        />
      ) : !err ? (
        <Empty text="正在取提案…" />
      ) : null}
    </div>
  );
}
