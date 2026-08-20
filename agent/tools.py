"""agent.tools：把我们的 api 包成 agentic 的 Tool。

**给什么工具就等于给什么权限**（§6 P1）。这里只给：
- `observe`      读当前观察包（ADR-0009 的"当前事实"；规则是替换而非追加）
- `write_surface` 读"能做什么 / 为什么不能做"（禁止清单连原因一起给，省得它反复试）
- `propose`      推一条草稿，等用户审批

**不给** `queue_op` / `set_worker_quota`：那是直接改状态。agent 想改就写提案。
"""
from __future__ import annotations

import json

from agentic.types import Tool

from agent.client import ApiClient, ApiError

#: 观察包里给 LLM 的文本上限：再长就是噪声，而且挤掉后续轮的空间
OBSERVATION_CHARS = 6000


def make_tools(client: ApiClient, *, source: str = "live") -> list[Tool]:
    async def observe(_args: dict) -> str:
        try:
            obs = client.observation(source=source)
        except ApiError as exc:
            return f"取观察失败：{exc}"
        text = obs.get("text") or ""
        facts = json.dumps(obs.get("facts", {}), ensure_ascii=False)
        return (text[:OBSERVATION_CHARS]
                + f"\n\n[机器可读] {facts}\n"
                + f"[提醒] 提案/命令里的 based_on_seq 用 {obs.get('seq')}")

    async def write_surface(_args: dict) -> str:
        try:
            return json.dumps(client.agent_tools(), ensure_ascii=False, indent=1)
        except ApiError as exc:
            return f"取写面清单失败：{exc}"

    async def propose(args: dict) -> str:
        body = {
            "kind": args.get("kind") or "production_queue",
            "author": "agent",
            "title_zh": args.get("title_zh") or "",
            "rationale_zh": args.get("rationale_zh") or "",
            "target": args.get("target") or {"queue": "main"},
            "hunks": args.get("hunks") or [],
        }
        if not body["rationale_zh"].strip():
            return "拒绝：rationale_zh 必填 —— 没有理由的提案用户无法判断，后端也会拒（§6 P3）"
        if not body["hunks"]:
            return "拒绝：hunks 不能为空 —— 提案必须给出可应用的改动，不能只描述想法"
        try:
            p = client.propose(body)
        except ApiError as exc:
            return f"提案被拒：{exc.detail}"
        v = p.get("validation") or {}
        if v.get("ok"):
            return (f"提案已提交：{p['id']}（等用户审批）。"
                    f"预览={(p.get('preview') or {}).get('kind', '无')}")
        errs = "；".join(e.get("text_zh", "") for e in v.get("errors", []))
        return (f"提案 {p['id']} 已提交但**校验未通过**：{errs}。"
                "它对用户仍然可见，但不可接受 —— 想让它可接受就修掉这些问题再提一条。")

    return [
        Tool(
            name="observe",
            description=("读当前观察包（经济/部队/生产/策略/风险/投影）。"
                         "先调它再做判断；它给的 seq 就是提案要回填的 based_on_seq。"),
            parameters={"type": "object", "properties": {}, "additionalProperties": False},
            function=observe,
        ),
        Tool(
            name="write_surface",
            description="读「能做什么 / 为什么不能做」的清单（含不支持的操作及原因）。",
            parameters={"type": "object", "properties": {}, "additionalProperties": False},
            function=write_surface,
        ),
        Tool(
            name="propose",
            description=("推一条草稿提案给用户审批。你**只能**这样改变局面 —— "
                         "没有直接下命令的工具。hunks 必须是可应用的操作。"),
            parameters={
                "type": "object",
                "properties": {
                    "kind": {"type": "string", "enum": ["production_queue"],
                             "description": "V1 只有 production_queue 能被应用"},
                    "title_zh": {"type": "string", "description": "一句话说清改什么"},
                    "rationale_zh": {"type": "string",
                                     "description": "**必填**：为什么这么改（用户靠它判断）"},
                    "target": {"type": "object", "description": '如 {"queue": "main"}'},
                    "hunks": {
                        "type": "array",
                        "description": ("可应用的改动。每条："
                                        '{"id","kind":"insert|delete|modify|reorder","text_zh","payload"}；'
                                        'payload：insert/modify 用 {"index","item"}，'
                                        'delete 用 {"index"}，reorder 用 {"order":[…]}（0..n-1 的排列）。'
                                        'item 形如 {"op":"build|train|assign_workers",'
                                        '"type":"terran/xxx","count":1,'
                                        '"placement":{"kind":"in_region","region":"home"}}'),
                        "items": {"type": "object"},
                    },
                },
                "required": ["title_zh", "rationale_zh", "hunks"],
                "additionalProperties": False,
            },
            function=propose,
        ),
    ]
