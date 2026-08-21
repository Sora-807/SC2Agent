"""view.proposals：草稿提案的存储 + 校验 + 应用（B7）。

这是 **agent 的唯一产出面**（`plan-frontend.md` §6）：agent 写 flow / 改队列 / 划区域
全部经此通道，`author="agent"`；用户审批。它不是 UI 专用结构 —— UI 和 agent 发的是同一份 JSON。

七条审批红线（§6 P1-P7）在这里落地：
- P1 agent 只能推提案，不能直接改状态 → 应用只发生在 `accept()`；
- P2 `validation.ok=false` 的提案**不可接受**，但必须可见（agent 要学、用户要诊断）；
- P3 拒绝**必须能附理由**，理由回流给 agent（否则它会重复推同一个提案）；
- P4 接受走与 agent 相同的命令路径（这里直接调会话的 `queue_op`）；
- P5 `anchor` 过期 → 自动"已失效"，**禁止盲接受**；
- P6 用户自己的编辑直接生效（不走提案），但同样过 validate 门；
- P7 `params`/`flow_ast` 提案 V1 只作用于离线草稿（`live_editable` 已被 DSL-T3 删掉）。

存储：JSONL 追加（每条一行，最新状态由内存索引维护）。选它不是图省事 ——
提案的价值一半在**历史**（agent 提过什么、为什么被拒），而追加日志天然保留历史。
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from game.catalog import Catalog
from game.production import QueueItem, QueueOp, WorkerTask
from game.production import PlacementExact, PlacementInRegion

#: anchor 允许的最大滞后（游戏秒）。超过就是"提案基于的世界已经不在了"（§6 P5）。
#: 比命令的 MAX_STALE_SEQ 宽松得多：提案是给人看的，看几十秒是正常的。
ANCHOR_STALE_SECONDS = 60.0

STATUS_PENDING = "待审批"
STATUS_ACCEPTED = "已接受"
STATUS_PARTIAL = "部分接受"
STATUS_REJECTED = "已拒绝"
STATUS_STALE = "已失效"

#: V1 支持"接受即应用"的提案类型。其余类型可以存、可以看，但不能 apply（并说明原因）。
APPLICABLE_KINDS = {"production_queue"}
KIND_LIMITS = {
    "map_plan": "地图规划是离线画板的产物，接受它需要 F9 的 map_plan patch 模型",
    "flow_ast": "flow 提交必须 validate + compile（R6），且 live 不能编辑 Strategy（R5）",
    "flow_commit": "同上：flow 提交走版本树，不在 live 会话里应用",
    "params": "参数热改需要 live_editable 声明，而它已被 DSL-T3 删除（等 hot-edit 轮）",
    "group_composition": "改 composition 会重置该实例的 lease（结构参数），等 hot-edit 轮",
}


@dataclass
class Hunk:
    """可逐条接受的最小单元。`payload` 是**可应用的操作**，不是自由文本。"""

    id: str
    kind: str            # insert / delete / reorder / modify
    text_zh: str
    payload: dict = field(default_factory=dict)


@dataclass
class Proposal:
    id: str
    author: str
    created_at: float
    anchor: dict | None
    kind: str
    title_zh: str
    rationale_zh: str
    target: dict
    hunks: list[Hunk]
    validation: dict | None = None
    preview: dict | None = None
    status: str = STATUS_PENDING
    decision: dict | None = None

    def to_json(self) -> dict:
        return {
            "id": self.id, "author": self.author, "created_at": self.created_at,
            "anchor": self.anchor, "kind": self.kind, "title_zh": self.title_zh,
            "rationale_zh": self.rationale_zh, "target": self.target,
            "hunks": [{"id": h.id, "kind": h.kind, "text_zh": h.text_zh, "payload": h.payload}
                      for h in self.hunks],
            "validation": self.validation, "preview": self.preview,
            "status": self.status, "decision": self.decision,
        }


# ---------------- hunk 应用（纯函数，便于单测） ----------------

def apply_hunks(items: list[QueueItem], hunks: list[Hunk]) -> list[QueueItem]:
    """把选中的 hunk 按顺序作用到队列上，返回**新列表**（不改入参）。

    每个 hunk 的 payload 形态：
    - insert  `{index, item}`   在 index 处插入
    - delete  `{index}`         删除 index
    - modify  `{index, item}`   替换 index
    - reorder `{order}`         order 必须是当前长度的一个排列

    刻意**按顺序逐个应用**而不是先算总位移：部分接受时下标语义才和用户看到的一致。
    """
    out = list(items)
    for h in hunks:
        p = h.payload
        if h.kind == "insert":
            idx = int(p.get("index", len(out)))
            if not 0 <= idx <= len(out):
                raise ValueError(f"hunk {h.id}: insert 下标 {idx} 越界（0..{len(out)}）")
            out.insert(idx, parse_item(p.get("item") or {}))
        elif h.kind == "delete":
            idx = int(p["index"])
            if not 0 <= idx < len(out):
                raise ValueError(f"hunk {h.id}: delete 下标 {idx} 越界（0..{len(out) - 1}）")
            out.pop(idx)
        elif h.kind == "modify":
            idx = int(p["index"])
            if not 0 <= idx < len(out):
                raise ValueError(f"hunk {h.id}: modify 下标 {idx} 越界（0..{len(out) - 1}）")
            out[idx] = parse_item(p.get("item") or {})
        elif h.kind == "reorder":
            order = [int(x) for x in p["order"]]
            if sorted(order) != list(range(len(out))):
                raise ValueError(
                    f"hunk {h.id}: reorder 的 order 必须是 0..{len(out) - 1} 的排列，收到 {order}")
            out = [out[i] for i in order]
        else:
            raise ValueError(f"hunk {h.id}: 未知 kind {h.kind!r}（insert|delete|modify|reorder）")
    return out


def parse_item(raw: dict) -> QueueItem:
    """JSON → QueueItem。未知 op / 未知 task 直接报错（不静默丢字段）。"""
    try:
        op = QueueOp(str(raw.get("op")))
    except ValueError:
        raise ValueError(
            f"未知队列 op {raw.get('op')!r}（合法值 {[e.value for e in QueueOp]}）") from None
    task = None
    if raw.get("task") is not None:
        try:
            task = WorkerTask(str(raw["task"]))
        except ValueError:
            raise ValueError(f"未知 worker task {raw['task']!r}") from None
    placement = None
    p = raw.get("placement")
    if isinstance(p, dict):
        if p.get("kind") == "exact":
            placement = PlacementExact(mark=str(p["mark"]))
        elif p.get("kind") == "in_region":
            placement = PlacementInRegion(region=str(p["region"]), index=p.get("index"))
        else:
            raise ValueError(f"未知 placement.kind {p.get('kind')!r}（exact|in_region）")
    return QueueItem(op=op, type=raw.get("type"), count=int(raw.get("count", 1)),
                     placement=placement, task=task)


def item_to_json(item: QueueItem) -> dict:
    placement: dict | None = None
    if isinstance(item.placement, PlacementExact):
        placement = {"kind": "exact", "mark": item.placement.mark}
    elif isinstance(item.placement, PlacementInRegion):
        placement = {"kind": "in_region", "region": item.placement.region,
                     "index": item.placement.index}
    return {
        "op": item.op.value if isinstance(item.op, QueueOp) else str(item.op),
        "type": item.type, "count": item.count, "placement": placement,
        "task": item.task.value if isinstance(item.task, WorkerTask) else item.task,
    }


# ---------------- 存储 ----------------

class ProposalStore:
    """提案存储 + 校验 + 应用。会话可为 None（纯离线看提案）。"""

    def __init__(self, catalog: Catalog, path: Path | str | None = None,
                 session: Any = None) -> None:
        self.catalog = catalog
        self.session = session
        self.path = Path(path) if path else None
        self._items: dict[str, Proposal] = {}
        if self.path is not None and self.path.is_file():
            self._load()

    # ---- 读 ----

    def list(self) -> list[dict]:
        """按**插入顺序**返回，不按 `created_at` 排。

        `created_at` 是**游戏时间**：会话重启后它从 0 重新开始，跨会话排序毫无意义 ——
        真机上表现为"刚被拒的提案排到了很旧的位置，于是回流窗口里看不到它"，
        agent 就会一遍遍重提同一件事（实测踩过）。dict 保序，插入序就是发生顺序。
        """
        self._expire()
        return [p.to_json() for p in self._items.values()]

    def get(self, pid: str) -> Proposal | None:
        self._expire()
        return self._items.get(pid)

    # ---- 写 ----

    def create(self, raw: dict) -> Proposal:
        """新建提案：**先校验，再落盘**。校验不通过也存（P2：不可接受但必须可见）。"""
        rationale = str(raw.get("rationale_zh") or "").strip()
        if not rationale:
            # P3 的对偶：没有理由的提案连提都不该提 —— 理由是审批的依据
            raise ValueError("提案必须带 rationale_zh（理由）：没有理由的提案不可接受（§6 P3）")
        kind = str(raw.get("kind") or "")
        hunks = [Hunk(id=str(h.get("id") or f"h{i}"), kind=str(h.get("kind") or ""),
                      text_zh=str(h.get("text_zh") or ""), payload=dict(h.get("payload") or {}))
                 for i, h in enumerate(raw.get("hunks") or [])]
        p = Proposal(
            id=str(raw.get("id") or "p-" + uuid.uuid4().hex[:8]),
            author=str(raw.get("author") or "agent"),
            created_at=float(raw.get("created_at") or self._now()),
            anchor=raw.get("anchor") or self._anchor(),
            kind=kind, title_zh=str(raw.get("title_zh") or ""),
            rationale_zh=rationale, target=dict(raw.get("target") or {}), hunks=hunks,
        )
        p.validation, p.preview = self.validate(p)
        self._items[p.id] = p
        self._append(p)
        return p

    def accept(self, pid: str, hunk_ids: list[str] | None = None,
               comment: str | None = None) -> Proposal:
        p = self._require(pid)
        if p.status == STATUS_STALE:
            raise ValueError("提案已失效（它基于的世界已经不在了）—— 请让 agent 基于当前状态重提（§6 P5）")
        if p.status in (STATUS_ACCEPTED, STATUS_PARTIAL, STATUS_REJECTED):
            raise ValueError(f"提案已处理（{p.status}）")
        if not (p.validation or {}).get("ok"):
            errs = "；".join(e.get("text_zh", "") for e in (p.validation or {}).get("errors", []))
            raise ValueError(f"校验未通过的提案不可接受（§6 P2）：{errs}")
        chosen = [h for h in p.hunks if hunk_ids is None or h.id in hunk_ids]
        if not chosen:
            raise ValueError("没有选中任何 hunk")
        self._apply(p, chosen)
        p.status = STATUS_ACCEPTED if len(chosen) == len(p.hunks) else STATUS_PARTIAL
        p.decision = {"at": self._now(), "accepted_hunks": [h.id for h in chosen],
                      "comment_zh": comment}
        self._append(p)
        return p

    def reject(self, pid: str, comment: str) -> Proposal:
        """拒绝**必须**附理由：理由回流给 agent，否则它会重复推同一个提案（§6 P3）。"""
        if not str(comment or "").strip():
            raise ValueError("拒绝必须附理由（§6 P3：理由回流给 agent 作为下一轮输入）")
        p = self._require(pid)
        p.status = STATUS_REJECTED
        p.decision = {"at": self._now(), "accepted_hunks": [], "comment_zh": comment}
        self._append(p)
        return p

    # ---- 校验 + 预览 ----

    def validate(self, p: Proposal) -> tuple[dict, dict | None]:
        errors: list[dict] = []
        preview: dict | None = None

        if p.kind not in APPLICABLE_KINDS:
            reason = KIND_LIMITS.get(p.kind, f"未知提案类型 {p.kind!r}")
            errors.append({"hunk_id": None, "text_zh": f"V1 还不能应用这类提案：{reason}"})
            return {"ok": False, "errors": errors}, None

        if not p.hunks:
            errors.append({"hunk_id": None, "text_zh": "提案没有任何 hunk（没东西可接受）"})

        queue_name = str(p.target.get("queue") or "main")
        current = self._current_items(queue_name)
        proposed: list[QueueItem] | None = None
        try:
            proposed = apply_hunks(current, p.hunks)
        except ValueError as exc:
            errors.append({"hunk_id": None, "text_zh": str(exc)})

        if proposed is not None:
            for i, item in enumerate(proposed):
                if item.type and self.catalog.by_stable_id(item.type) is None:
                    errors.append({"hunk_id": None,
                                   "text_zh": f"第 {i} 项的类型 {item.type!r} catalog 没登记"})
            if not errors:
                preview = {"kind": "projection_pair",
                           "current_ref": f"proposal:{p.id}:current",
                           "proposed_ref": f"proposal:{p.id}:proposed"}
        return {"ok": not errors, "errors": errors}, preview

    def preview_pair(self, pid: str, *, horizon: float = 120.0) -> dict:
        """双投影：当前队列 vs 提案后的队列。**接受前先看未来** —— §6 的杀手功能。

        通过 `session.project(items)` 算：离线会话本地算，live 会话让**子进程**算
        （GameState 在子进程里，父进程只有帧）。所以这里不直接碰 `session.world`。
        """
        p = self._require(pid)
        if self.session is None:
            raise ValueError("没有会话，算不了投影")
        project = getattr(self.session, "project", None)
        if project is None:
            raise ValueError("会话不支持投影")
        queue_name = str(p.target.get("queue") or "main")
        current = self._current_items(queue_name)
        proposed = apply_hunks(current, p.hunks)

        cur_frame = project(list(current), name=queue_name, horizon=horizon)
        prop_frame = project(list(proposed), name=queue_name + "（提案后）", horizon=horizon)
        if cur_frame is None or prop_frame is None:
            raise ValueError("会话没有算出投影（可能还没有帧）")
        return {"proposal_id": p.id, "current": cur_frame, "proposed": prop_frame}

    # ---- 内部 ----

    def _apply(self, p: Proposal, hunks: list[Hunk]) -> None:
        """应用 = 算出结果队列后走**与 agent 相同的命令路径**（§6 P4），不开后门。"""
        if self.session is None:
            raise ValueError("没有运行中的会话，无法应用提案")
        queue_name = str(p.target.get("queue") or "main")
        items = apply_hunks(self._current_items(queue_name), hunks)
        self.session.queue_op("submit", queue_name, items=items)

    def _current_items(self, queue_name: str) -> list[QueueItem]:
        if self.session is None:
            return []
        # 走会话三件套协议：离线会话读 runtime，live 会话从最近一帧反解
        get = getattr(self.session, "queue_items", None)
        if get is None:
            return []
        return list(get(queue_name))

    def _anchor(self) -> dict | None:
        if self.session is None:
            return None
        # 会话协议：seq 与 game_time 是两种会话都有的属性（离线=世界，live=父进程追踪到的）
        return {"seq": int(self.session.seq), "game_time": round(float(self.session.game_time), 3)}

    def _now(self) -> float:
        return round(float(self.session.game_time), 3) if self.session is not None else 0.0

    def _expire(self) -> None:
        """anchor 过期 → 自动"已失效"（§6 P5：禁止盲接受）。"""
        if self.session is None:
            return
        now = float(self.session.game_time)
        for p in self._items.values():
            if p.status != STATUS_PENDING or not p.anchor:
                continue
            if now - float(p.anchor.get("game_time", now)) > ANCHOR_STALE_SECONDS:
                p.status = STATUS_STALE
                self._append(p)

    def _require(self, pid: str) -> Proposal:
        """取提案，**并先重判新鲜度**（§6 P5：禁止盲接受）。

        之前 `_expire()` 只在 `list()`/`get()` 里调，于是 `accept()` 有个洞：
        anchor 早已过期、但期间没人拉过列表 → 状态还是"待审批" → 照单全收，
        等于拿过期观察改世界（同时违反 R8 的精神）。live 下前端不轮询 proposals，
        这个窗口尤其宽。放在 `_require` 里 = 每条写路径（accept/reject）都自动受保护。
        """
        self._expire()
        p = self._items.get(pid)
        if p is None:
            raise KeyError(f"没有提案 {pid!r}")
        return p

    def _append(self, p: Proposal) -> None:
        """追加一行。提案的价值一半在历史（提过什么、为什么被拒），追加日志天然保留。"""
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(p.to_json(), ensure_ascii=False, separators=(",", ":")))
            fh.write("\n")

    def _load(self) -> None:
        assert self.path is not None
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            raw = json.loads(line)
            hunks = [Hunk(id=h["id"], kind=h["kind"], text_zh=h["text_zh"],
                          payload=h.get("payload") or {}) for h in raw.get("hunks") or []]
            self._items[raw["id"]] = Proposal(
                id=raw["id"], author=raw["author"], created_at=raw["created_at"],
                anchor=raw.get("anchor"), kind=raw["kind"], title_zh=raw["title_zh"],
                rationale_zh=raw["rationale_zh"], target=raw.get("target") or {},
                hunks=hunks, validation=raw.get("validation"), preview=raw.get("preview"),
                status=raw.get("status", STATUS_PENDING), decision=raw.get("decision"),
            )