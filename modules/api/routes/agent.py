"""agent 面：观察包（B10）+ 对话（P3 切片 A）+ 记忆笔记 + 写面清单。"""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from api.session import MAX_STALE_SEQ
from api.state import require_source
from game.catalog import load_all
from view.encode import to_json
from view.observe import frames_by_topic, observation_packet
from view.statics import schema_static

router = APIRouter()


@router.get("/api/observation")
def observation(request: Request, source: str = Query("live"),
                text: bool = Query(False)) -> dict:
    """当前观察包（ADR-0009）。`text=true` 额外给可直接进 prompt 的渲染文本。

    规则是**替换**而不是追加：每次取都是一份新的"当前事实"，旧的靠 `supersedes` 指向。
    `facts.based_on_seq` 是下命令时必须回填的东西（R8 的闭环）。
    """
    state = request.app.state
    src = require_source(state, source)
    info = src.info()
    frames = frames_by_topic(src.latest_at(info.to_time))
    packet = observation_packet(frames, catalog=load_all(), supersedes=state.last_observation_seq)
    state.last_observation_seq = packet.seq
    body = {
        "seq": packet.seq, "game_time": packet.game_time,
        "supersedes": packet.supersedes,
        "sections": packet.sections, "facts": packet.facts,
    }
    if text:
        body["text"] = packet.render()
    return body


# ---- agent 对话（P3 切片 A：离线商量回路） ----

@router.get("/api/agent/chat")
def agent_chat_history(request: Request) -> dict:
    talk = request.app.state.agent_talk
    if talk is None:
        return {"enabled": False, "reason": "未启用（serve_api --agent-base）",
                "messages": []}
    talk.refresh_running_steps()   # 流式动效的燃料（本轮进行中步骤）
    out = talk.describe()
    out["enabled"] = True
    out["error"] = getattr(talk, "_engine_error", None)
    return out


@router.post("/api/agent/chat")
async def agent_chat_say(body: dict, request: Request) -> dict:
    talk = request.app.state.agent_talk
    if talk is None:
        raise HTTPException(status_code=503,
                            detail="对话服务未启用（serve_api --agent-base）")
    text = str(body.get("text") or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="要说点什么（空消息）")
    out = await talk.say(text)
    if out.get("error"):
        # 503：不是请求写错，是对话后端没就绪（G7：理由必须显形）
        raise HTTPException(status_code=503, detail=out["error"])
    return out


@router.post("/api/agent/chat/stream")
async def agent_chat_say_stream(body: dict, request: Request):
    """SSE 流式对话（2026-08-22 十五轮：接 BaseAgent start_stream）。

    事件流：delta（思考/正文/工具参数分片，从第一个 token 起就有）→
    tool_call（工具完成，带结果摘要）→ run_end → round（终态：回复/改动/历史）。
    引擎跑在 agent-talk 专属循环线程，事件经跨线程队列递过来 —— 这里只做搬运。
    """
    talk = request.app.state.agent_talk
    if talk is None:
        raise HTTPException(status_code=503,
                            detail="对话服务未启用（serve_api --agent-base）")
    text = str(body.get("text") or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="要说点什么（空消息）")
    handle = talk.start_round(text)

    async def gen():
        while True:
            # 工具执行可能长时间无事件（试算/真机启动），超时给足而不是掐流
            ev = await asyncio.to_thread(handle.events.get, True, 300)
            if ev is None:
                break
            yield "data: " + json.dumps(ev, ensure_ascii=False) + "\n\n"

    return StreamingResponse(
        gen(), media_type="text/event-stream",
        headers={"cache-control": "no-store", "x-accel-buffering": "no"},
    )


# ---- agent 记忆笔记：已退役（2026-08-23，I19/I20）----
# 记忆统一走 agent scratch 的 memory/ 结构化文件（跨会话、可 grep、按生命周期分文件）；
# 旧 jsonl 是无结构 append（同一偏好重复写 3 次的实例就在 archive 里）。端点保留
# 兼容已存在的调用方，write_surface / 提示词不再宣传它 —— 别给 Agent 接工具。

@router.get("/api/agent/notes")
def agent_notes_list(request: Request) -> list[dict]:
    path: Path = request.app.state.agent_notes_path
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                out.append(json.loads(line))
            except ValueError:
                continue    # 坏行跳过：记忆不该因为一行坏数据整个读不出
    return out


@router.post("/api/agent/notes")
def agent_note_save(body: dict, request: Request) -> dict:
    text = str(body.get("text") or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="笔记内容不能为空")
    note = {"title_zh": str(body.get("title_zh") or text[:24]),
            "text": text, "at": time.time()}
    path: Path = request.app.state.agent_notes_path
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(note, ensure_ascii=False) + "\n")
    return note


@router.get("/api/agent/tools")
def agent_tools() -> dict:
    """agent 的**写面**清单：与 UI 完全同一套入口（决策 U7 / §6 P4）。

    刻意把"能做什么"和"为什么不能做"放在一起 —— agent 最容易犯的错是
    试一个不存在的动作，然后在错误里反复打转。
    """
    schema = to_json(schema_static())
    return {
        "commands": [
            {"method": "POST", "path": "/api/commands/queue/{op}",
             "ops": ["submit", "append", "prepend", "insert", "replace_head",
                     "clear", "remove", "reorder"],
             "body": {"based_on_seq": "必填（取自观察包 facts.based_on_seq）",
                      "name": "队列名，默认 main",
                      "items": "submit/append/prepend/insert/replace_head 用",
                      "index": "remove/insert 用（insert：剩余队列位置，0=队首前）",
                      "order": "reorder 用（0..n-1 的排列）"},
             "note": "队列 op 轻量、不走 validate/compile；执行时按 constraint 门控（S11）。"
                     "插入天然只具后效性 —— 已执行项不在队列里（BUILD 进在途、TRAIN 直接走）"},
            {"method": "POST", "path": "/api/commands/workers",
             "body": {"based_on_seq": "必填", "task": "mineral|gas|idle",
                      "count": "**维持** N 个（目标值、幂等），不是再派 N 个"}},
            {"method": "POST", "path": "/api/proposals",
             "body": {"kind": "production_queue（V1 只有这个能应用）",
                      "title_zh": "一句话", "rationale_zh": "**必填**：没有理由的提案不可接受",
                      "target": {"queue": "main"},
                      "hunks": "[{id, kind: insert|delete|modify|reorder, text_zh, payload}]"},
             "note": "改变别人计划的事走提案通道，由用户审批；直接下命令只适合明确授权的操作"},
        ],
        "rules": [
            "所有命令必带 based_on_seq；落后超过阈值会返 409 并回报当前 seq —— 重取观察再试（R8）",
            "策略是文件（strategies/<id>.yaml，可写，保存即编译校验；常用打法用 imports 引"
            " strategies/_lib.yaml 模板库）。新会话装配生效；对局中换策略走 "
            "POST /api/session/swap?strategy=<id>（同名 step 续位，group_slots 必须一致）",
            "loadout（runtime/loadouts/<id>.yaml）一发装配：session/start?loadout=<id> = "
            "地图规划 + 策略 + 生产序列自动入队",
            "flow 提交必须 validate + compile（R6）；生产队列 op 不需要",
            "不支持的东西会返 400 并带原因，别重试同一个动作",
        ],
        "readable": {
            # 读面清单（I20 文件契约闭环）：与文件工作区的只读区一一对应。
            # agent 靠它知道"我能翻哪些历史"，不必试探。
            "recordings/index.md": "对局记录清单（每局一条：族 vs 族 · 地图 · 时长）",
            "recordings/<id>.md": "单局摘要：时间线（建筑落成/警报/策略转移）+ 终局盘点。"
                                  "原始帧流 .jsonl 刻意不挂（几 MB），要细节看摘要",
            "maps/<源>/<bbox>.md": "地图格点网格（I18）：源 = live 或地图规划 id；bbox = "
                                  "x1_y1_x2_y2（左下+右上）。`·`空可建 `✗`障碍 D/R/F+序=槽位 "
                                  "gas/CC/M=预设点。maps/index.md 看源清单与示例",
            "traces/": "你此前会话的执行轨迹（trace.md / summary.json，白名单内）—— 跨会话翻旧账",
            "proposals/log.jsonl": "提案审计史（append-only，含被拒理由）",
        },
        "unsupported": {
            "queue_ops": schema["queue"]["unsupported_ops"],
            "flow": schema["forbidden"],
        },
        "max_stale_seq": MAX_STALE_SEQ,
    }
