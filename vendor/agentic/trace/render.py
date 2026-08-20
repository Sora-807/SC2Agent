"""trace → 自包含 HTML 可视化。

视觉与布局参考 DSH web 轨迹浏览器:
- 顶部:run 信息 + token 卡片。
- 页签:概览 / 时间线 / 轨迹 / 对话 / 文件 / 事件。
- 时间线页签 = 全局 mini timeline(Chrome-Network 风格)+ 按 agent 分泳道的详情时间线。
- 轨迹页签 = DSH 风格 ledger 表格(时间 / agent / 类型标签 / 内容 / 指标 / 耗时)。
- 文件页签 = trace 目录清单 + workspace 快照(如果 trace 目录下存在 workspace/)。
"""
from __future__ import annotations

import json
from pathlib import Path

from . import events

MAX_WORKSPACE_FILES = 200
MAX_WORKSPACE_FILE_BYTES = 2 * 1024 * 1024


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _read_workspace(directory: Path) -> dict[str, str]:
    """读取 trace 目录下的 workspace 快照(如果有)。

    优先读 workspace/ 目录;没有目录时回退到 workspace_snapshots/final.json。
    """
    root = directory / "workspace"
    if root.is_dir():
        files: dict[str, str] = {}
        for path in sorted(root.rglob("*")):
            if path.is_file() and len(files) < MAX_WORKSPACE_FILES:
                try:
                    if path.stat().st_size > MAX_WORKSPACE_FILE_BYTES:
                        continue
                    text = path.read_text(encoding="utf-8")
                except (UnicodeDecodeError, OSError):
                    continue
                relative = str(path.relative_to(root)).replace("\\", "/")
                files[relative] = text
        return files
    final_snapshot = directory / "workspace_snapshots" / "final.json"
    if final_snapshot.exists():
        try:
            loaded = json.loads(final_snapshot.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                return {str(key): str(value) for key, value in loaded.items()}
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def _trace_file_list(directory: Path) -> list[dict]:
    """列出 trace 目录自身文件,供文件页签展示。"""
    result: list[dict] = []
    for path in sorted(directory.rglob("*")):
        if path.is_file():
            try:
                size = path.stat().st_size
            except OSError:
                size = 0
            result.append({
                "path": str(path.relative_to(directory)).replace("\\", "/"),
                "size_bytes": size,
            })
    return result


def _trace_file_contents(directory: Path, files: list[dict]) -> dict[str, str]:
    """读取 trace 目录下小型文本文件内容,供文件页签直接展示。

    agents/*.jsonl 与 messages 不在这里重复读取,前端从 TRACE.agents 生成。
    """
    contents: dict[str, str] = {}
    total_bytes = 0
    for file in files:
        path = file["path"]
        if path == "trace.html":
            continue
        if path.startswith("agents/"):
            continue
        if file["size_bytes"] > 512 * 1024:
            continue
        try:
            text = (directory / path).read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        if total_bytes + len(text.encode("utf-8")) > 2 * 1024 * 1024:
            continue
        total_bytes += len(text.encode("utf-8"))
        contents[path] = text
    return contents


def load_trace(trace_dir: str | Path) -> dict:
    """把一个 trace 目录的全部产物读成一个 dict(供渲染)。"""
    directory = Path(trace_dir)
    if not (directory / "agents").exists():
        raise FileNotFoundError(f"{directory} is not a trace dir (no agents/ subdir)")

    def load_json(name: str) -> dict:
        path = directory / name
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}

    meta = load_json("run.meta.json")
    summary = load_json("summary.json")
    tree = load_json("tree.json") or {"roots": [], "edges": []}

    agents: dict[str, dict] = {}
    reasoning: dict[str, str] = {}
    agents_dir = directory / "agents"
    targets = sorted(
        file.stem for file in agents_dir.glob("*.jsonl")
        if not file.name.endswith(".messages.jsonl")
    )
    for target in targets:
        event_list = _read_jsonl(agents_dir / f"{target}.jsonl")
        messages = _read_jsonl(agents_dir / f"{target}.messages.jsonl")
        for event in event_list:
            reference = event.get("reasoning_ref")
            if reference and reference not in reasoning:
                reasoning_path = directory / reference
                if reasoning_path.exists():
                    reasoning[reference] = reasoning_path.read_text(encoding="utf-8")
        agents[target] = {"events": event_list, "messages": messages}

    trace_files = _trace_file_list(directory)
    # 附加工作区回放数据:初始/最终快照 + 每次文件修改的 before/after
    workspace_replay = None
    try:
        from .replay import load_final_snapshot, load_initial_snapshot, replay_workspace
        initial_snapshot = load_initial_snapshot(directory)
        final_snapshot = load_final_snapshot(directory)
        replay_result = replay_workspace(
            initial_snapshot,
            {agent: info["events"] for agent, info in agents.items()},
        )
        workspace_replay = {
            "initial": initial_snapshot,
            "final": final_snapshot,
            "files": replay_result.files,
            "steps": replay_result.workspace_steps,
            "comparison": replay_result.compare(final_snapshot or None),
        }
    except Exception:  # noqa: BLE001 — 回放失败不阻断渲染
        workspace_replay = None
    return {
        "meta": meta,
        "summary": summary,
        "tree": tree,
        "agents": agents,
        "reasoning": reasoning,
        "trace_files": trace_files,
        "trace_file_contents": _trace_file_contents(directory, trace_files),
        "workspace_files": _read_workspace(directory),
        "workspace_replay": workspace_replay,
    }


_CSS = """
:root{
  --bg:#f5f6f8; --panel:#ffffff; --panel-2:#fafbfc; --panel-3:#f2f4f7;
  --border-l1:rgba(0,0,0,.04); --border-l2:rgba(0,0,0,.1); --border-l3:rgba(0,0,0,.16);
  --text:#0f1115; --text-secondary:#3d3f43; --muted:#7d828a; --caption:#a7adb5;
  --blue:#4176e6; --blue-strong:#2b5fd9; --blue-soft:#edf3fd;
  --green:#16a34a; --green-soft:#e6f6ec;
  --amber:#d97706; --amber-soft:#fff4e5;
  --red:#dc2626; --red-soft:#fee2e2;
  --purple:#7c3aed; --purple-soft:#f1eafd;
  --shadow:0 1px 2px rgba(0,0,0,.04), 0 1px 3px rgba(0,0,0,.06);
  --sans:-apple-system,BlinkMacSystemFont,'Segoe UI','PingFang SC','Hiragino Sans GB','Microsoft YaHei','Helvetica Neue',Helvetica,Arial,sans-serif;
  --mono:'SF Mono','JetBrains Mono','Fira Code',Consolas,'Liberation Mono',Menlo,Courier,'PingFang SC','Microsoft YaHei';
}
body[data-theme=dark]{
  --bg:#111215; --panel:#191a1f; --panel-2:#1e2026; --panel-3:#26282f;
  --border-l1:rgba(255,255,255,.04); --border-l2:rgba(255,255,255,.1); --border-l3:rgba(255,255,255,.18);
  --text:#f3f4f6; --text-secondary:#d5d7db; --muted:#9aa0a8; --caption:#6e747d;
  --blue:#5b8def; --blue-strong:#7ba4f4; --blue-soft:#1f2a3f;
  --green:#34d399; --green-soft:#123326;
  --amber:#fbbf24; --amber-soft:#3a2c12;
  --red:#f87171; --red-soft:#3f1d1d;
  --purple:#a78bfa; --purple-soft:#2e1f47;
  --shadow:none;
}
*{box-sizing:border-box}
html,body{margin:0;height:100%}
body{font-family:var(--sans);color:var(--text);background:var(--bg);font-size:13px;display:flex;flex-direction:column;overflow:hidden}
.muted{color:var(--muted)}
.mono{font-family:var(--mono)}
/* 头部 */
#header{flex:0 0 auto;background:var(--panel);border-bottom:1px solid var(--border-l2);padding:12px 16px 8px}
#header h2{margin:0 0 6px;font-size:16px;font-weight:650;letter-spacing:.01em}
#runinfo{font-size:12px;line-height:1.7;color:var(--text-secondary)}
.cards{display:flex;gap:10px;margin-top:10px;flex-wrap:wrap}
.card{background:var(--panel-2);border:1px solid var(--border-l2);border-radius:8px;padding:8px 14px;min-width:112px;box-shadow:var(--shadow)}
.card .number{font-size:18px;font-weight:700;font-variant-numeric:tabular-nums}
.card .label{color:var(--muted);font-size:11px;margin-top:1px}
.card.accent .number{color:var(--blue)}
/* 页签工具条 */
#tabs{flex:0 0 auto;display:flex;align-items:center;gap:2px;background:var(--panel);border-bottom:1px solid var(--border-l2);padding:0 12px;height:38px}
.tab{padding:8px 13px;cursor:pointer;border-bottom:2px solid transparent;color:var(--muted);user-select:none;font-size:12.5px;border-radius:6px 6px 0 0}
.tab:hover{color:var(--text);background:var(--panel-2)}
.tab.active{color:var(--blue);border-bottom-color:var(--blue);font-weight:600}
#toolbar-right{margin-left:auto;display:flex;align-items:center;gap:6px}
#theme-toggle{border:1px solid var(--border-l2);background:var(--panel-2);color:var(--text-secondary);border-radius:6px;height:24px;padding:0 10px;cursor:pointer;font-size:12px}
#theme-toggle:hover{background:var(--panel-3)}
#detail{flex:0 0 auto;background:var(--panel-2);border-bottom:1px solid var(--border-l1);padding:6px 16px;font-size:12px;min-height:30px;color:var(--text-secondary)}
/* 面板 */
.panel{flex:1 1 0;min-height:0;overflow:auto;background:var(--panel)}
.hidden{display:none !important}
.section-title{font-weight:650;padding:10px 16px 6px;font-size:12px;color:var(--text-secondary)}
/* 概览 */
#overview{display:grid;grid-template-columns:minmax(280px,380px) minmax(420px,1fr);gap:14px;padding:12px 16px}
@media(max-width:900px){#overview{grid-template-columns:1fr}}
.overview-box{background:var(--panel-2);border:1px solid var(--border-l2);border-radius:10px;padding:12px 14px;box-shadow:var(--shadow)}
.overview-box h3{margin:0 0 8px;font-size:12px;color:var(--muted);font-weight:600}
.clitree{font-family:var(--mono);font-size:12px;margin:0;white-space:pre;line-height:1.7;color:var(--text-secondary)}
.agent-table{width:100%;border-collapse:collapse;font-size:12px}
.agent-table th{position:sticky;top:0;background:var(--panel-2);color:var(--muted);font-weight:500;text-align:left;padding:5px 8px;border-bottom:1px solid var(--border-l2)}
.agent-table td{padding:5px 8px;border-bottom:1px solid var(--border-l1);color:var(--text-secondary);font-variant-numeric:tabular-nums}
.agent-table tr:hover td{background:var(--panel-3)}
/* mini timeline */
#mini-wrap{flex:0 0 auto;border-bottom:1px solid var(--border-l2);background:var(--panel-2);padding:8px 16px}
#mini-title{font-size:11px;color:var(--muted);margin-bottom:6px}
#mini-plot{position:relative;height:52px;background:var(--panel-3);border:1px solid var(--border-l1);border-radius:8px;overflow:hidden;cursor:crosshair;user-select:none}
#mini-track{position:absolute;top:6px;bottom:6px;left:0;right:0}
.mini-span{position:absolute;height:12px;border-radius:2px;opacity:.82}
.mini-span.llm{background:var(--blue)}
.mini-span.tool{background:var(--green)}
.mini-span.dispatch{background:var(--red);width:2px}
.mini-ruler{position:absolute;top:46px;height:6px;border-left:1px solid var(--border-l2);font-size:9px;color:var(--caption);padding-left:3px;white-space:nowrap}
/* 泳道 */
#timeline-toolbar{display:flex;align-items:center;gap:6px;padding:8px 16px;border-bottom:1px solid var(--border-l1);background:var(--panel)}
.zoom-btn{border:1px solid var(--border-l2);background:var(--panel-2);color:var(--text-secondary);border-radius:6px;height:24px;min-width:26px;cursor:pointer;font-size:12px}
.zoom-btn:hover{background:var(--panel-3)}
#swim-wrap{flex:1 1 0;min-height:0;overflow:auto;background:var(--panel-2);position:relative}
#swim-content{position:relative;padding:8px 0}
.swim-lane{position:relative;height:44px;border-bottom:1px solid var(--border-l1);white-space:nowrap}
.swim-label{position:sticky;left:0;display:inline-block;width:180px;height:44px;line-height:44px;background:var(--panel);border-right:1px solid var(--border-l2);font-size:11px;padding:0 10px;overflow:hidden;z-index:5;cursor:pointer;vertical-align:top;box-shadow:1px 0 2px rgba(0,0,0,.03);font-weight:550}
.swim-label:hover{background:var(--blue-soft)}
.swim-span{position:absolute;border-radius:3px;font-size:9px;color:#fff;overflow:hidden;white-space:nowrap;line-height:16px;padding:0 4px;cursor:pointer;font-weight:600}
.swim-span.llm{background:var(--blue);z-index:3}
.swim-span.tool{background:var(--green);z-index:3}
.swim-span.run{background:var(--panel-3);border:1px solid var(--border-l2);z-index:1;height:42px;top:1px}
.swim-dot{position:absolute;top:2px;font-size:10px;color:var(--red);z-index:4;cursor:default}
/* 轨迹表格 */
#trajectory-wrap{padding:8px 0}
#trajectory-table{width:100%;border-collapse:collapse;font-size:12px;table-layout:fixed}
#trajectory-table th{position:sticky;top:0;z-index:3;background:var(--panel);color:var(--muted);font-weight:500;text-align:left;padding:6px 8px;border-bottom:1px solid var(--border-l2);white-space:nowrap}
#trajectory-table td{padding:5px 8px;border-bottom:1px solid var(--border-l1);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:var(--text-secondary);font-variant-numeric:tabular-nums}
#trajectory-table tr:hover td{background:var(--panel-2);cursor:pointer}
#trajectory-table tr[data-selected=true] td{background:var(--blue-soft)}
.tag{display:inline-flex;align-items:center;height:19px;padding:0 6px;border-radius:5px;font-size:11px;font-weight:600}
.tag.llm{color:var(--blue);background:var(--blue-soft)}
.tag.tool{color:var(--green);background:var(--green-soft)}
.tag.turn{color:var(--purple);background:var(--purple-soft)}
.tag.dispatch{color:var(--red);background:var(--red-soft)}
.tag.run{color:var(--text-secondary);background:var(--panel-3)}
.tag.timeout{color:var(--amber);background:var(--amber-soft)}
/* 对话 */
#conv{display:flex;flex-direction:row;height:100%}
#conv-sidebar{flex:0 0 25%;min-width:190px;max-width:320px;overflow-y:auto;border-right:1px solid var(--border-l2);background:var(--panel-2)}
#conv-tree{padding:6px 10px 20px}
#conv-tree .agent-row{display:block;width:100%;text-align:left;border:0;background:transparent;color:var(--text-secondary);cursor:pointer;padding:5px 8px;border-radius:6px;font-size:12px;font-family:var(--mono);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
#conv-tree .agent-row:hover{background:var(--panel-3)}
#conv-tree .agent-row.active{background:var(--blue-soft);color:var(--blue);font-weight:600}
#conv-main{flex:1 1 0;min-width:0;display:flex;flex-direction:column}
#convhead{padding:8px 16px;background:var(--panel-2);border-bottom:1px solid var(--border-l1);font-size:12px;z-index:2}
#convscroll{flex:1 1 0;overflow-y:auto}
.msgrow{display:flex;align-items:flex-start;border-bottom:1px solid var(--border-l1);padding:5px 16px}
.msgrow.target{background:var(--amber-soft)}
.msg-idx{width:38px;color:var(--caption);font-size:11px;flex-shrink:0;font-family:var(--mono)}
.msg-role{width:76px;font-weight:700;font-size:11px;flex-shrink:0}
.role-system{color:var(--muted)}.role-user{color:var(--green)}.role-assistant{color:var(--blue)}.role-tool{color:var(--amber)}
.msg-content{margin:0;font-size:12px;white-space:pre-wrap;word-break:break-word;flex:1;font-family:var(--mono);color:var(--text)}
.msg-tools{margin:2px 0;color:var(--text-secondary);font-size:11px}
.reasoning{max-height:200px;overflow:auto;background:var(--panel-3);padding:8px;font-size:11px;margin:4px 0;border-radius:6px}
.think-sum{cursor:pointer;color:var(--blue);font-size:11px;margin:2px 0;display:inline-block}
/* 文件 */
#files-wrap{padding:12px 16px;display:grid;grid-template-columns:280px minmax(0,1fr);gap:12px;height:100%}
#file-tree{overflow:auto;background:var(--panel-2);border:1px solid var(--border-l2);border-radius:8px;padding:10px 12px;font-family:var(--mono);font-size:12px}
#file-tree .file-row{display:block;width:100%;text-align:left;border:0;background:transparent;color:var(--text-secondary);cursor:pointer;padding:2px 4px;border-radius:4px;font-family:var(--mono);font-size:12px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
#file-tree .file-row:hover{background:var(--panel-3)}
#file-tree .file-row.active{background:var(--blue-soft);color:var(--blue)}
#file-content{overflow:auto;background:var(--panel-2);border:1px solid var(--border-l2);border-radius:8px;padding:12px}
#file-content pre{margin:0;white-space:pre-wrap;word-break:break-word;font-family:var(--mono);font-size:12px}
/* 回放 */
#replay-wrap{display:grid;grid-template-columns:300px minmax(0,1fr);height:100%}
#replay-sidebar{overflow:auto;background:var(--panel-2);border-right:1px solid var(--border-l2)}
#replay-sidebar .step-row{display:block;width:100%;text-align:left;border:0;border-bottom:1px solid var(--border-l1);background:transparent;color:var(--text-secondary);cursor:pointer;padding:7px 10px;font-size:12px}
#replay-sidebar .step-row:hover{background:var(--panel-3)}
#replay-sidebar .step-row.active{background:var(--blue-soft);color:var(--blue)}
#replay-sidebar .step-tool{font-weight:700}
#replay-main{overflow:auto;padding:10px 14px;background:var(--panel)}
#replay-filebar{display:flex;align-items:center;gap:8px;margin-bottom:8px;font-size:12px;color:var(--text-secondary)}
#replay-file-select{height:26px;border:1px solid var(--border-l2);border-radius:6px;background:var(--panel-2);color:var(--text);padding:0 6px;font-size:12px}
#replay-step-title{font-size:12px;margin-bottom:6px;color:var(--text)}
#diff-empty{color:var(--muted);padding:20px;text-align:center}
.diff-table{width:100%;border-collapse:collapse;font-family:var(--mono);font-size:12px;table-layout:fixed}
.diff-table td{vertical-align:top;padding:0 8px;line-height:1.5;white-space:pre-wrap;word-break:break-all}
.diff-line-no{width:44px;color:var(--caption);text-align:right;user-select:none;white-space:nowrap}
.diff-left,.diff-right{width:calc(50% - 44px)}
.diff-delete .diff-left{background:var(--red-soft)}
.diff-delete .diff-line-no{background:var(--red-soft)}
.diff-insert .diff-right{background:var(--green-soft)}
.diff-insert .diff-line-no{background:var(--green-soft)}
.diff-equal .diff-left,.diff-equal .diff-right{color:var(--text-secondary)}
.diff-collapse td{text-align:center;padding:6px 0;background:var(--panel-2)}
.diff-collapse button{border:1px solid var(--border-l2);background:var(--panel);color:var(--blue);border-radius:5px;padding:2px 10px;cursor:pointer;font-size:11px}
.diff-collapse button:hover{background:var(--blue-soft)}
/* 事件 */
#events-wrap{padding:12px 16px}
#event-search{width:260px;height:26px;border:1px solid var(--border-l2);border-radius:6px;background:var(--panel-2);color:var(--text);padding:0 8px;font-size:12px;margin-bottom:8px}
.event-row{border-bottom:1px solid var(--border-l1);padding:4px 0;white-space:pre-wrap;word-break:break-all;font-family:var(--mono);font-size:12px;color:var(--text-secondary)}
"""

_JS = r"""
const TRACE = JSON.parse(document.getElementById('trace-data').textContent);
const fmt = value => (value==null||value===undefined)?'':String(value);
function escapeHtml(value){ return fmt(value).replace(/[&<>"']/g, char=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char])); }
function formatTokens(number){ number=Number(number); if(!isFinite(number)) return ''; if(number>=1e6) return (number/1e6).toFixed(1)+'M'; if(number>=1e3) return (number/1e3).toFixed(1)+'k'; return String(number); }
function formatDuration(milliseconds){ milliseconds=Number(milliseconds); if(!isFinite(milliseconds)) return ''; if(milliseconds<1000) return milliseconds+'ms'; return (milliseconds/1000).toFixed(1)+'s'; }
function formatTime(value){ if(!value) return ''; const date=new Date(value); if(isNaN(date.getTime())) return String(value); return date.toLocaleTimeString(undefined,{hour:'2-digit',minute:'2-digit',second:'2-digit',fractionalSecondDigits:3}); }
const timeOf = value => value ? new Date(value).getTime() : NaN;
function isNumber(value){ return typeof value==='number' && !isFinite(value); }
function isFiniteNumber(value){ return typeof value==='number' && isFinite(value); }
const EV = {RUN_START:'run_start',TURN_START:'turn_start',LLM_CALL:'llm_call',TOOL_CALL:'tool_call',TURN_END:'turn_end',RUN_END:'run_end',DISPATCH:'dispatch',LLM_TIMEOUT:'llm_timeout'};
const LABEL_WIDTH = 180;
let selectedAgent = null;
let selectedTimelineScale = 1;

function agentOrder(){
  const edges=TRACE.tree.edges||[]; const children={};
  for(const edge of edges){ (children[edge.parent]=children[edge.parent]||[]).push(edge.child); }
  const roots=(TRACE.tree.roots&&TRACE.tree.roots.length)?TRACE.tree.roots:Object.keys(TRACE.agents);
  const order=[]; const seen=new Set();
  function visit(agent){ if(seen.has(agent)||!TRACE.agents[agent])return; seen.add(agent); order.push(agent); (children[agent]||[]).forEach(visit); }
  roots.forEach(visit);
  Object.keys(TRACE.agents).forEach(agent=>{ if(!seen.has(agent)) order.push(agent); });
  return order;
}
function typeOf(agent){ const found=(TRACE.agents[agent]?.events||[]).find(event=>event.type===EV.RUN_START); return (found||{}).type_key||''; }
function timeRange(){
  let start=Infinity,end=-Infinity;
  for(const agent of Object.keys(TRACE.agents)) for(const event of TRACE.agents[agent].events)
    for(const key of ['ts','started_at']){ const t=timeOf(event[key]); if(isFiniteNumber(t)){ if(t<start)start=t; if(t>end)end=t; } }
  if(!(start<Infinity)){ start=0; end=1; }
  if(end<=start) end=start+1;
  return [start,end];
}
function kindLabel(type){
  switch(type){
    case EV.LLM_CALL: return 'LLM';
    case EV.TOOL_CALL: return 'TOOL';
    case EV.TURN_START: return 'TURN';
    case EV.TURN_END: return 'TURN';
    case EV.RUN_START: return 'RUN';
    case EV.RUN_END: return 'RUN';
    case EV.DISPATCH: return 'DISPATCH';
    case EV.LLM_TIMEOUT: return 'TIMEOUT';
    default: return type;
  }
}
function kindCssClass(type){
  if(type===EV.LLM_CALL) return 'llm';
  if(type===EV.TOOL_CALL) return 'tool';
  if(type===EV.TURN_START||type===EV.TURN_END) return 'turn';
  if(type===EV.RUN_START||type===EV.RUN_END) return 'run';
  if(type===EV.DISPATCH) return 'dispatch';
  if(type===EV.LLM_TIMEOUT) return 'timeout';
  return 'run';
}
function eventText(event){
  if(event.type===EV.LLM_CALL) return event.model || 'llm_call';
  if(event.type===EV.TOOL_CALL) return event.tool || 'tool_call';
  if(event.type===EV.DISPATCH) return 'dispatch → '+(event.target||'');
  if(event.type===EV.RUN_START) return 'type='+(event.type_key||'')+' target='+(event.target||'');
  if(event.type===EV.RUN_END) return 'outcome='+(event.outcome||'')+(event.reason?(' reason='+event.reason):'');
  return '';
}
function eventMetrics(event){
  if(event.type===EV.LLM_CALL) return 'in '+formatTokens(event.input_tokens)+' · out '+formatTokens(event.output_tokens);
  return '';
}
function renderHeader(){
  const meta=TRACE.meta||{}, run=(TRACE.summary&&TRACE.summary.run)||{};
  const agentCount=Object.keys(TRACE.agents).length;
  document.getElementById('runinfo').innerHTML =
    '<h2>'+escapeHtml(meta.run_id)+'</h2>'+
    '<div>开始 '+escapeHtml(meta.started_at)+' ｜ 结束 '+escapeHtml(meta.ended_at)+' ｜ agents '+agentCount+'</div>'+
    '<div class="cards">'+
    '<div class="card accent"><div class="number">'+formatTokens(run.input_tokens)+'</div><div class="label">输入 token</div></div>'+
    '<div class="card"><div class="number">'+formatTokens(run.cached_input)+'</div><div class="label">缓存命中</div></div>'+
    '<div class="card"><div class="number">'+formatTokens(run.uncached_input)+'</div><div class="label">未命中</div></div>'+
    '<div class="card"><div class="number">'+formatTokens(run.output_tokens)+'</div><div class="label">输出 token</div></div>'+
    '<div class="card"><div class="number">'+fmt(run.turns)+'</div><div class="label">轮次</div></div>'+
    '<div class="card"><div class="number">'+fmt(run.tool_calls)+'</div><div class="label">工具调用</div></div>'+
    '<div class="card"><div class="number" style="color:'+((Number(run.tool_failures)||0)>0?'var(--red)':'var(--text)')+'">'+fmt(Number(run.tool_failures)||0)+'</div><div class="label">工具失败</div></div>'+
    '</div>';
}
function renderOverview(){
  const children={}; for(const edge of (TRACE.tree.edges||[])) (children[edge.parent]=children[edge.parent]||[]).push(edge.child);
  const roots=(TRACE.tree.roots&&TRACE.tree.roots.length)?TRACE.tree.roots:Object.keys(TRACE.agents);
  const treeLines=[];
  function walkTree(node,prefix){
    const kids=children[node]||[];
    kids.forEach((kid,index)=>{ const last=index===kids.length-1;
      treeLines.push(prefix+(last?'└── ':'├── ')+kid+' ('+typeOf(kid)+')');
      walkTree(kid,prefix+(last?'    ':'│   '));
    });
  }
  roots.forEach(root=>{ treeLines.push(root+' ('+typeOf(root)+')'); walkTree(root,''); });
  document.getElementById('tree').innerHTML='<pre class="clitree">'+escapeHtml(treeLines.join('\n'))+'</pre>';

  const agentStats=TRACE.summary&&TRACE.summary.agents||{};
  let rows='';
  for(const agent of agentOrder()){
    const stat=agentStats[agent]||{};
    const tools=stat.tools||{};
    const failedTools=stat.failed_tools||{};
    const topTools=Object.entries(tools).sort((a,b)=>b[1]-a[1]).slice(0,4).map(([name,count])=>name+' '+count).join(' · ')||'';
    const topFailedTools=Object.entries(failedTools).sort((a,b)=>b[1]-a[1]).slice(0,4).map(([name,count])=>name+' '+count).join(' · ')||'';
    rows+='<tr><td>'+escapeHtml(agent)+'</td><td>'+escapeHtml(typeOf(agent))+'</td><td>'+formatTokens(stat.input_tokens)+'</td><td>'+formatTokens(stat.cached_input)+'</td><td>'+formatTokens(stat.output_tokens)+'</td><td>'+fmt(stat.turns)+'</td><td>'+fmt(stat.tool_calls)+'</td><td style="color:'+((Number(stat.tool_failures)||0)>0?'var(--red)':'var(--text-secondary)')+'">'+fmt(Number(stat.tool_failures)||0)+'</td><td>'+escapeHtml(topTools)+'</td><td>'+escapeHtml(topFailedTools)+'</td></tr>';
  }
  document.getElementById('agent-table-body').innerHTML=rows;
}
function renderMiniTimeline(){
  const [start,end]=timeRange(); const duration=end-start;
  const plot=document.getElementById('mini-plot');
  const width=plot.clientWidth||800;
  const pxPerMs=width/duration;
  let html='';
  for(const agent of Object.keys(TRACE.agents)) for(const event of TRACE.agents[agent].events){
    if(event.type===EV.LLM_CALL||event.type===EV.TOOL_CALL){
      const s=timeOf(event.started_at),e=timeOf(event.ts);
      if(!isFiniteNumber(s)||!isFiniteNumber(e)) continue;
      html+='<div class="mini-span '+kindCssClass(event.type)+'" style="left:'+((s-start)*pxPerMs)+'px;width:'+Math.max((e-s)*pxPerMs,2)+'px" title="'+escapeHtml(agent+' · '+event.type+' turn '+event.turn_no+' · '+formatDuration(event.duration_ms))+'"></div>';
    } else if(event.type===EV.DISPATCH){
      const t=timeOf(event.ts); if(isFiniteNumber(t)) html+='<div class="mini-span dispatch" style="left:'+((t-start)*pxPerMs)+'px" title="'+escapeHtml('dispatch → '+event.target)+'"></div>';
    }
  }
  const ticks=5;
  let ruler='';
  for(let i=0;i<=ticks;i++){
    const t=start+duration*i/ticks;
    const left=(i/ticks)*100;
    ruler+='<div class="mini-ruler" style="left:'+left+'%">'+formatTime(new Date(t).toISOString())+'</div>';
  }
  document.getElementById('mini-track').innerHTML=html;
  document.getElementById('mini-ruler').innerHTML=ruler;
}
function renderSwimlane(){
  const [start,end]=timeRange(); const duration=end-start;
  const container=document.getElementById('swim-wrap');
  const width=Math.max((container.clientWidth||800)*1.6*selectedTimelineScale, (duration*0.06*selectedTimelineScale));
  const pxPerMs=width/duration;
  document.getElementById('swim-content').style.width=(LABEL_WIDTH+width)+'px';
  let html='';
  for(const agent of agentOrder()){
    const data=TRACE.agents[agent]; if(!data)continue;
    let row='<div class="swim-lane"><span class="swim-label" data-agent="'+escapeHtml(agent)+'">'+escapeHtml(agent)+'</span>';
    const runStart=data.events.find(event=>event.type===EV.RUN_START);
    const runEnd=data.events.find(event=>event.type===EV.RUN_END);
    if(runStart&&runEnd){ const s=timeOf(runStart.ts),e=timeOf(runEnd.ts); if(isFiniteNumber(s)&&isFiniteNumber(e)) row+='<div class="swim-span run" style="left:'+(LABEL_WIDTH+(s-start)*pxPerMs)+'px;width:'+Math.max((e-s)*pxPerMs,2)+'px"></div>'; }
    for(const event of data.events){
      if(event.type===EV.LLM_CALL||event.type===EV.TOOL_CALL){
        const s=timeOf(event.started_at),e=timeOf(event.ts); if(!isFiniteNumber(s)||!isFiniteNumber(e))continue;
        const cssClass=kindCssClass(event.type), top=event.type===EV.LLM_CALL?4:24;
        const label=event.type===EV.LLM_CALL?'LLM':event.tool;
        row+='<div class="swim-span '+cssClass+'" style="left:'+(LABEL_WIDTH+(s-start)*pxPerMs)+'px;width:'+Math.max((e-s)*pxPerMs,6)+'px;top:'+top+'px" data-agent="'+escapeHtml(agent)+'" data-kind="'+event.type+'" data-turn="'+event.turn_no+'" title="'+escapeHtml(event.type+' turn '+event.turn_no+' · '+formatDuration(event.duration_ms))+'">'+escapeHtml(label)+'</div>';
      } else if(event.type===EV.DISPATCH){ const t=timeOf(event.ts); if(isFiniteNumber(t)) row+='<div class="swim-dot" style="left:'+(LABEL_WIDTH+(t-start)*pxPerMs)+'px" title="'+escapeHtml('dispatch → '+event.target)+'">▼</div>'; }
    }
    row+='</div>'; html+=row;
  }
  document.getElementById('swim-content').innerHTML=html;
  document.querySelectorAll('.swim-label').forEach(label=>label.addEventListener('click',()=>selectAgent(label.dataset.agent,null)));
  document.querySelectorAll('.swim-span[data-kind]').forEach(span=>span.addEventListener('click',()=>onSpanClick(span.dataset.agent,span.dataset.kind,span.dataset.turn)));
  let drag=null;
  const scroller=document.getElementById('swim-wrap');
  scroller.onmousedown=event=>{ if(event.target.classList.contains('swim-span')||event.target.classList.contains('swim-label'))return; drag={x:event.pageX,left:scroller.scrollLeft}; scroller.classList.add('grabbing'); };
  window.onmousemove=event=>{ if(!drag)return; scroller.scrollLeft=drag.left-(event.pageX-drag.x); };
  window.onmouseup=()=>{ if(drag){drag=null; scroller.classList.remove('grabbing');} };
}
function renderTrajectory(){
  const rows=[];
  for(const agent of Object.keys(TRACE.agents)) for(const event of TRACE.agents[agent].events){
    rows.push({agent,event,ts:timeOf(event.started_at||event.ts)});
  }
  rows.sort((a,b)=>a.ts-b.ts);
  let html='';
  rows.forEach((row,index)=>{
    const event=row.event;
    const cssClass=kindCssClass(event.type);
    const timeText=formatTime(event.started_at||event.ts);
    html+='<tr data-agent="'+escapeHtml(row.agent)+'" data-kind="'+escapeHtml(event.type)+'" data-turn="'+escapeHtml(event.turn_no||'')+'" data-index="'+index+'">'+
      '<td style="width:40px">'+index+'</td>'+
      '<td style="width:100px">'+escapeHtml(timeText)+'</td>'+
      '<td style="width:130px">'+escapeHtml(row.agent)+'</td>'+
      '<td style="width:90px"><span class="tag '+cssClass+'">'+escapeHtml(kindLabel(event.type))+'</span></td>'+
      '<td>'+escapeHtml(eventText(event))+'</td>'+
      '<td style="width:130px">'+escapeHtml(eventMetrics(event))+'</td>'+
      '<td style="width:80px">'+escapeHtml(formatDuration(event.duration_ms))+'</td>'+
      '</tr>';
  });
  document.getElementById('trajectory-body').innerHTML=html;
  document.querySelectorAll('#trajectory-body tr').forEach(row=>row.addEventListener('click',()=>onTrajectoryRowClick(row)));
}
function onTrajectoryRowClick(row){
  document.querySelectorAll('#trajectory-body tr').forEach(item=>item.dataset.selected='false');
  row.dataset.selected='true';
  const agent=row.dataset.agent, kind=row.dataset.kind, turn=row.dataset.turn;
  renderDetail(agent,kind,turn);
  if(kind===EV.LLM_CALL){ const event=TRACE.agents[agent].events.find(item=>item.type===EV.LLM_CALL&&String(item.turn_no)===String(turn)); selectAgent(agent,event?event.input_count:null); }
}
function renderDetail(agent,kind,turn){
  const data=TRACE.agents[agent]; const event=data&&data.events.find(item=>item.type===kind&&String(item.turn_no)===String(turn));
  let html='<b>'+escapeHtml(kindLabel(kind))+'</b> · agent '+escapeHtml(agent)+' · turn '+escapeHtml(turn);
  if(event){
    html+='　';
    const keys=kind===EV.LLM_CALL?['model','input_tokens','cached_tokens','uncached_tokens','output_tokens','duration_ms','input_count']:['tool','duration_ms'];
    const tokenKeys={input_tokens:1,output_tokens:1,cached_tokens:1,uncached_tokens:1};
    for(const key of keys){ if(event[key]!=null){ const value=tokenKeys[key]?formatTokens(event[key]):event[key]; html+=escapeHtml(key)+':'+escapeHtml(value)+'　'; } }
    if(kind===EV.LLM_CALL){ const ref=event.reasoning_ref; if(ref&&TRACE.reasoning[ref]) html+='<details style="display:inline"><summary>reasoning</summary><pre class="reasoning">'+escapeHtml(TRACE.reasoning[ref])+'</pre></details>'; }
  }
  document.getElementById('detail').innerHTML=html;
}
function renderConversationTree(){
  const children={}; for(const edge of (TRACE.tree.edges||[])) (children[edge.parent]=children[edge.parent]||[]).push(edge.child);
  const roots=(TRACE.tree.roots&&TRACE.tree.roots.length)?TRACE.tree.roots:Object.keys(TRACE.agents);
  const lines=[];
  function walk(node,prefix,depth){
    lines.push({agent:node,label:prefix+node+' ('+typeOf(node)+')',depth});
    const kids=children[node]||[];
    kids.forEach((kid,index)=>{ const last=index===kids.length-1;
      walk(kid,prefix+(last?'    ':'│   '),depth+1);
    });
  }
  roots.forEach(root=>{ if(TRACE.agents[root]) walk(root,'',0); });
  Object.keys(TRACE.agents).forEach(agent=>{ if(!lines.some(item=>item.agent===agent)) lines.push({agent,label:agent+' ('+typeOf(agent)+')',depth:0}); });
  let html='';
  for(const item of lines){
    html+='<button class="agent-row" data-agent="'+escapeHtml(item.agent)+'" style="padding-left:'+(8+item.depth*12)+'px">'+escapeHtml(item.label)+'</button>';
  }
  document.getElementById('conv-tree').innerHTML=html;
  document.querySelectorAll('#conv-tree .agent-row').forEach(button=>button.addEventListener('click',()=>{
    selectedAgent=button.dataset.agent;
    updateConversationTreeActive();
    renderConversation(null);
  }));
  updateConversationTreeActive();
}
function updateConversationTreeActive(){
  document.querySelectorAll('#conv-tree .agent-row').forEach(button=>{
    button.classList.toggle('active', button.dataset.agent===selectedAgent);
  });
}
function renderConversation(scrollIndex){
  const agent=selectedAgent; const data=TRACE.agents[agent]; if(!data){ document.getElementById('convscroll').innerHTML=''; return; }
  updateConversationTreeActive();
  document.getElementById('convhead').innerHTML='<b>对话日志: '+escapeHtml(agent)+'</b> ('+escapeHtml(typeOf(agent))+') <span class="muted">· 点 LLM 行/span 定位到 input_count;assistant 消息可展开思考</span>';
  const reasoningMap={}; for(const event of data.events){ if(event.type===EV.LLM_CALL && event.input_count!=null && event.reasoning_ref) reasoningMap[event.input_count]=event.reasoning_ref; }
  let html='';
  for(const message of (data.messages||[])){
    let body=escapeHtml(message.content||'');
    if(message.tool_calls&&message.tool_calls.length) body+='<div class="msg-tools">'+message.tool_calls.map(call=>'→ <b>'+escapeHtml(call.name)+'</b>('+escapeHtml(JSON.stringify(call.args))+')').join('<br>')+'</div>';
    if(message.role==='assistant' && reasoningMap[message.idx]!=null && TRACE.reasoning[reasoningMap[message.idx]]){
      const reasoningText=TRACE.reasoning[reasoningMap[message.idx]];
      body+='<details><summary class="think-sum">思考 ('+reasoningText.length+' 字)</summary><pre class="reasoning">'+escapeHtml(reasoningText)+'</pre></details>';
    }
    html+='<div class="msgrow role-'+escapeHtml(message.role)+'" id="msg-'+message.idx+'"><span class="msg-idx">'+message.idx+'</span><span class="msg-role">'+escapeHtml(message.role)+'</span><pre class="msg-content">'+body+'</pre></div>';
  }
  const scroll=document.getElementById('convscroll'); scroll.innerHTML=html;
  if(scrollIndex!=null){ const row=document.getElementById('msg-'+scrollIndex); if(row){ row.scrollIntoView({block:'center'}); row.classList.add('target'); setTimeout(()=>row.classList.remove('target'),2200); } }
  else scroll.scrollTop=0;
}
function traceFileContent(path){
  if(path.startsWith('agents/')){
    const parts=path.split('/');
    if(parts.length===2 && parts[1].endsWith('.jsonl')){
      const agent=parts[1].replace(/\.jsonl$/,'');
      if(TRACE.agents[agent]) return TRACE.agents[agent].events.map(event=>JSON.stringify(event)).join('\n');
    }
    if(parts.length===2 && parts[1].endsWith('.messages.jsonl')){
      const agent=parts[1].replace(/\.messages\.jsonl$/,'');
      if(TRACE.agents[agent]) return TRACE.agents[agent].messages.map(message=>JSON.stringify(message)).join('\n');
    }
  }
  return (TRACE.trace_file_contents||{})[path]||'';
}
function renderFiles(){
  const workspaceFiles=TRACE.workspace_files||{};
  const workspacePaths=Object.keys(workspaceFiles);
  const traceFiles=TRACE.trace_files||[];
  let treeHtml='<div class="section-title">workspace 快照</div>';
  if(workspacePaths.length===0) treeHtml+='<div class="muted" style="padding:0 6px">(trace 目录下没有 workspace/)</div>';
  for(const path of workspacePaths){
    treeHtml+='<button class="file-row" data-file-type="workspace" data-file="'+escapeHtml(path)+'">📄 '+escapeHtml(path)+'</button>';
  }
  treeHtml+='<div class="section-title" style="padding-top:10px">trace 目录</div>';
  for(const file of traceFiles){
    treeHtml+='<button class="file-row" data-file-type="trace" data-file="'+escapeHtml(file.path)+'">📄 '+escapeHtml(file.path)+' <span class="muted">('+formatTokens(file.size_bytes)+'B)</span></button>';
  }
  document.getElementById('file-tree').innerHTML=treeHtml;
  document.querySelectorAll('#file-tree .file-row').forEach(button=>button.addEventListener('click',()=>{
    document.querySelectorAll('#file-tree .file-row').forEach(item=>item.classList.remove('active'));
    button.classList.add('active');
    const type=button.dataset.fileType, path=button.dataset.file;
    const content=type==='workspace' ? (workspaceFiles[path]||'') : traceFileContent(path);
    document.getElementById('file-content').innerHTML=content ? '<pre>'+escapeHtml(content)+'</pre>' : '<pre class="muted">该文件没有可内嵌展示的内容(可能过大或为二进制)。</pre>';
  }));
  const firstWorkspace=workspacePaths[0];
  if(firstWorkspace){
    document.getElementById('file-content').innerHTML='<pre>'+escapeHtml(workspaceFiles[firstWorkspace]||'')+'</pre>';
    document.querySelector('#file-tree .file-row').classList.add('active');
  } else {
    document.getElementById('file-content').innerHTML='<pre class="muted">选择左侧文件查看内容</pre>';
  }
}
function lineDiff(beforeText, afterText){
  const beforeLines=beforeText==null?'':String(beforeText);
  const afterLines=afterText==null?'':String(afterText);
  const a=beforeLines.split('\n'); if(a.length===1&&a[0]==='') a.pop();
  const b=afterLines.split('\n'); if(b.length===1&&b[0]==='') b.pop();
  const rows=[]; let row=[];
  for(let i=0;i<=a.length;i++){ row.push(0); }
  const dp=[row.slice()];
  for(let j=1;j<=b.length;j++){
    const current=[0];
    for(let i=1;i<=a.length;i++){
      if(a[i-1]===b[j-1]) current.push(dp[j-1][i-1]+1);
      else current.push(Math.max(dp[j-1][i], current[i-1]));
    }
    dp.push(current);
  }
  const ops=[];
  let i=a.length,j=b.length;
  while(i>0||j>0){
    if(i>0&&j>0&&a[i-1]===b[j-1]){ ops.unshift({type:'equal',beforeNumber:i,afterNumber:j,beforeLine:a[i-1],afterLine:b[j-1]}); i--; j--; }
    else if(j>0&&(i===0||dp[j][i-1]<=dp[j-1][i])){ ops.unshift({type:'insert',afterNumber:j,afterLine:b[j-1]}); j--; }
    else { ops.unshift({type:'delete',beforeNumber:i,beforeLine:a[i-1]}); i--; }
  }
  return ops;
}
function diffRowHtml(op){
  const leftNumber=op.beforeNumber!=null?op.beforeNumber:'';
  const rightNumber=op.afterNumber!=null?op.afterNumber:'';
  const leftText=op.beforeLine!=null?escapeHtml(op.beforeLine):'';
  const rightText=op.afterLine!=null?escapeHtml(op.afterLine):'';
  return '<tr class="diff-'+op.type+'"><td class="diff-line-no">'+leftNumber+'</td><td class="diff-left">'+leftText+'</td><td class="diff-line-no">'+rightNumber+'</td><td class="diff-right">'+rightText+'</td></tr>';
}
function renderDiffTable(beforeText, afterText){
  const ops=lineDiff(beforeText, afterText);
  if(ops.length===0) return '<div id="diff-empty">(文件为空,没有可展示的差异)</div>';
  let html='<table class="diff-table">';
  let equalRun=0;
  const collapseThreshold=4;
  for(let index=0;index<ops.length;index++){
    const op=ops[index];
    if(op.type==='equal'){ equalRun++; continue; }
    if(equalRun>0){
      if(equalRun>collapseThreshold){
        html+='<tr class="diff-collapse" data-collapse="1"><td colspan="4"><button class="diff-expand-btn" data-start="'+(index-equalRun)+'" data-count="'+equalRun+'">展开 '+equalRun+' 行未变化</button></td></tr>';
      } else {
        for(let k=index-equalRun;k<index;k++) html+=diffRowHtml(ops[k]);
      }
      equalRun=0;
    }
    html+=diffRowHtml(op);
  }
  if(equalRun>0){
    const start=ops.length-equalRun;
    if(equalRun>collapseThreshold) html+='<tr class="diff-collapse" data-collapse="1"><td colspan="4"><button class="diff-expand-btn" data-start="'+start+'" data-count="'+equalRun+'">展开 '+equalRun+' 行未变化</button></td></tr>';
    else for(let k=start;k<ops.length;k++) html+=diffRowHtml(ops[k]);
  }
  html+='</table>';
  return html;
}
function selectedReplayPath(){
  const select=document.getElementById('replay-file-select');
  return select?select.value:'';
}
function renderReplay(){
  const replay=TRACE.workspace_replay;
  const sidebar=document.getElementById('replay-sidebar');
  const main=document.getElementById('replay-main');
  if(!replay || !replay.steps || replay.steps.length===0){
    sidebar.innerHTML='<div class="muted" style="padding:10px">(没有可回放的修改步骤;新版本 trace 会自动保存 initial/final 快照)</div>';
    main.innerHTML='<div id="diff-empty">没有工作区变更可展示。运行一次 agent 后,回放页签会显示每次文件修改的前后 diff。</div>';
    return;
  }
  const steps=replay.steps;
  const paths=[...new Set(steps.map(step=>step.path).filter(Boolean))];
  let sidebarHtml='';
  steps.forEach((step,index)=>{
    sidebarHtml+='<button class="step-row" data-step="'+index+'"><span class="step-tool">'+escapeHtml(step.tool)+'</span> · '+escapeHtml(step.path)+'<br><span class="muted">#'+index+' · '+escapeHtml(step.agent)+' · '+escapeHtml(formatTime(step.started_at))+'</span></button>';
  });
  sidebar.innerHTML=sidebarHtml;
  main.innerHTML=
    '<div id="replay-filebar"><span>变更文件:</span><select id="replay-file-select">'+
    paths.map(path=>'<option value="'+escapeHtml(path)+'">'+escapeHtml(path)+'</option>').join('')+
    '</select><span class="muted">选择文件后,再选择左侧步骤查看该步骤的前后差异</span></div>'+
    '<div id="replay-step-title"></div><div id="replay-diff"></div>';
  sidebar.querySelectorAll('.step-row').forEach(button=>button.addEventListener('click',()=>{
    sidebar.querySelectorAll('.step-row').forEach(item=>item.classList.remove('active'));
    button.classList.add('active');
    selectReplayStep(Number(button.dataset.step));
  }));
  const fileSelect=document.getElementById('replay-file-select');
  fileSelect.addEventListener('change',()=>{
    const path=fileSelect.value;
    const first=steps.findIndex(step=>step.path===path);
    if(first>=0){ sidebar.querySelectorAll('.step-row').forEach(item=>item.classList.remove('active')); const row=sidebar.querySelector('.step-row[data-step="'+first+'"]'); if(row) row.classList.add('active'); selectReplayStep(first); }
  });
  // 默认选中第一个步骤
  const firstRow=sidebar.querySelector('.step-row');
  if(firstRow){ firstRow.classList.add('active'); selectReplayStep(0); }
}
function selectReplayStep(stepIndex){
  const replay=TRACE.workspace_replay;
  if(!replay) return;
  const step=replay.steps[stepIndex]; if(!step) return;
  document.getElementById('replay-step-title').innerHTML='<b>#'+stepIndex+'</b> · '+escapeHtml(step.tool)+' · '+escapeHtml(step.path)+' · '+escapeHtml(step.agent)+' · '+escapeHtml(formatTime(step.started_at))+' · event_seq '+escapeHtml(step.event_sequence);
  document.getElementById('replay-diff').innerHTML=renderDiffTable(step.before, step.after);
  document.querySelectorAll('.diff-expand-btn').forEach(button=>button.addEventListener('click',()=>expandDiff(button)));
}
function expandDiff(button){
  const start=Number(button.dataset.start), count=Number(button.dataset.count);
  const row=button.closest('tr');
  const ops=[]; // 重新计算当前文件的 diff
  const replay=TRACE.workspace_replay; const stepIndex=Number(document.querySelector('#replay-sidebar .step-row.active')?.dataset.step||0);
  const step=replay?replay.steps[stepIndex]:null;
  if(!step) return;
  const allOps=lineDiff(step.before, step.after);
  let html='';
  for(let k=start;k<start+count;k++) html+=diffRowHtml(allOps[k]);
  row.insertAdjacentHTML('beforebegin', html);
  row.remove();
}
function renderEvents(){
  const searchInput=document.getElementById('event-search');
  const query=(searchInput?searchInput.value||'':'').toLowerCase();
  let html='';
  for(const agent of Object.keys(TRACE.agents)){
    for(const event of TRACE.agents[agent].events){
      const raw=JSON.stringify(event);
      if(query && !(agent+' '+raw).toLowerCase().includes(query)) continue;
      html+='<div class="event-row">'+escapeHtml(agent)+' · '+escapeHtml(event.type)+' · '+escapeHtml(raw)+'</div>';
    }
  }
  document.getElementById('events-wrap').innerHTML='<input id="event-search" placeholder="搜索事件(agent / json)" value="'+escapeHtml(query)+'"><div id="event-list">'+html+'</div>';
  document.getElementById('event-search').addEventListener('input',renderEvents);
}
function selectAgent(agent,scrollIndex){ selectedAgent=agent; renderConversation(scrollIndex); }
function onSpanClick(agent,kind,turn){ renderDetail(agent,kind,turn); if(kind===EV.LLM_CALL){ const event=TRACE.agents[agent].events.find(item=>item.type===EV.LLM_CALL&&String(item.turn_no)===String(turn)); selectAgent(agent,event?event.input_count:null); } }
function switchTab(tab){
  document.querySelectorAll('.tab').forEach(item=>item.classList.remove('active'));
  document.querySelectorAll('.panel').forEach(item=>item.classList.add('hidden'));
  document.getElementById(tab+'-tab').classList.add('active');
  document.getElementById(tab+'-panel').classList.remove('hidden');
  if(tab==='timeline'){ renderMiniTimeline(); renderSwimlane(); }
  if(tab==='trajectory') renderTrajectory();
  if(tab==='conversation'){ renderConversationTree(); renderConversation(null); }
  if(tab==='files') renderFiles();
  if(tab==='replay') renderReplay();
  if(tab==='events') renderEvents();
}
// init
selectedAgent=(TRACE.tree.roots&&TRACE.tree.roots[0])||agentOrder()[0]||Object.keys(TRACE.agents)[0];
renderHeader(); renderOverview(); renderMiniTimeline(); renderSwimlane(); renderTrajectory(); renderConversation(null); renderFiles();
const laneCount=document.querySelectorAll('.swim-lane').length, spanCount=document.querySelectorAll('.swim-span[data-kind]').length;
document.getElementById('detail').innerHTML='<span class="muted">泳道 '+laneCount+' 条 / span '+spanCount+' 个 — 点击 span 或轨迹行查看详情;点 LLM span 切换对话并定位。</span>';
window.addEventListener('resize',()=>{ renderMiniTimeline(); renderSwimlane(); });
document.getElementById('overview-tab').addEventListener('click',()=>switchTab('overview'));
document.getElementById('timeline-tab').addEventListener('click',()=>switchTab('timeline'));
document.getElementById('trajectory-tab').addEventListener('click',()=>switchTab('trajectory'));
document.getElementById('conversation-tab').addEventListener('click',()=>switchTab('conversation'));
document.getElementById('files-tab').addEventListener('click',()=>switchTab('files'));
document.getElementById('replay-tab').addEventListener('click',()=>switchTab('replay'));
document.getElementById('events-tab').addEventListener('click',()=>switchTab('events'));
document.getElementById('theme-toggle').addEventListener('click',()=>{
  const body=document.body;
  body.dataset.theme=body.dataset.theme==='dark'?'light':'dark';
});
document.getElementById('swim-zoom-out').addEventListener('click',()=>{ selectedTimelineScale=Math.max(0.5,selectedTimelineScale/1.4); renderSwimlane(); });
document.getElementById('swim-zoom-in').addEventListener('click',()=>{ selectedTimelineScale=Math.min(8,selectedTimelineScale*1.4); renderSwimlane(); });
document.getElementById('swim-zoom-reset').addEventListener('click',()=>{ selectedTimelineScale=1; renderSwimlane(); });
"""


def render_trace_html(trace_dir: str | Path, out_path: str | Path | None = None) -> str:
    """渲染一个 trace 目录为自包含 HTML(内嵌数据 + 前端)。返回写入的路径。"""
    data = load_trace(trace_dir)
    json_text = json.dumps(data, ensure_ascii=False, default=str).replace("<", "\\u003c")
    title = (data.get("meta") or {}).get("run_id", "trace")
    html = (
        "<!doctype html>\n<html><head><meta charset=\"utf-8\">\n"
        "<title>trace " + title + "</title>\n<style>" + _CSS + "</style>\n</head>\n<body>\n"
        "<div id=\"header\"><div id=\"runinfo\"></div></div>\n"
        "<div id=\"tabs\">"
        "<div class=\"tab active\" id=\"overview-tab\">概览</div>"
        "<div class=\"tab\" id=\"timeline-tab\">时间线</div>"
        "<div class=\"tab\" id=\"trajectory-tab\">轨迹</div>"
        "<div class=\"tab\" id=\"conversation-tab\">对话</div>"
        "<div class=\"tab\" id=\"files-tab\">文件</div>"
        "<div class=\"tab\" id=\"replay-tab\">回放</div>"
        "<div class=\"tab\" id=\"events-tab\">事件</div>"
        "<div id=\"toolbar-right\">"
        "<button id=\"theme-toggle\" title=\"切换亮/暗主题\">暗色</button>"
        "</div>"
        "</div>\n"
        "<div id=\"detail\" class=\"muted\"></div>\n"
        "<div id=\"overview-panel\" class=\"panel\"><div id=\"overview\">"
        "<div class=\"overview-box\"><h3>Agent 树</h3><div id=\"tree\"></div></div>"
        "<div class=\"overview-box\"><h3>Agent 统计</h3>"
        "<table class=\"agent-table\"><thead><tr><th>target</th><th>type</th><th>输入</th><th>缓存</th><th>输出</th><th>轮次</th><th>工具</th><th>失败</th><th>主要工具</th><th>失败工具</th></tr></thead>"
        "<tbody id=\"agent-table-body\"></tbody></table></div>"
        "</div></div>\n"
        "<div id=\"timeline-panel\" class=\"panel hidden\" style=\"display:flex;flex-direction:column\">"
        "<div id=\"mini-wrap\"><div id=\"mini-title\">全局时间线(Chrome-Network 风格 · 点击泳道 span 查看详情)</div>"
        "<div id=\"mini-plot\"><div id=\"mini-track\"></div><div id=\"mini-ruler\"></div></div></div>"
        "<div id=\"timeline-toolbar\"><span class=\"muted\">泳道缩放</span>"
        "<button id=\"swim-zoom-out\" class=\"zoom-btn\">−</button>"
        "<button id=\"swim-zoom-in\" class=\"zoom-btn\">+</button>"
        "<button id=\"swim-zoom-reset\" class=\"zoom-btn\">1:1</button></div>"
        "<div id=\"swim-wrap\"><div id=\"swim-content\"></div></div>"
        "</div>\n"
        "<div id=\"trajectory-panel\" class=\"panel hidden\"><div id=\"trajectory-wrap\">"
        "<table id=\"trajectory-table\"><thead><tr><th style=\"width:40px\">#</th><th style=\"width:100px\">时间</th>"
        "<th style=\"width:130px\">agent</th><th style=\"width:90px\">类型</th><th>内容</th>"
        "<th style=\"width:130px\">指标</th><th style=\"width:80px\">耗时</th></tr></thead>"
        "<tbody id=\"trajectory-body\"></tbody></table></div></div>\n"
        "<div id=\"conversation-panel\" class=\"panel hidden\"><div id=\"conv\">"
        "<div id=\"conv-sidebar\"><div class=\"section-title\">Agent 树</div><div id=\"conv-tree\"></div></div>"
        "<div id=\"conv-main\"><div id=\"convhead\"></div><div id=\"convscroll\"></div></div>"
        "</div></div>\n"
        "<div id=\"files-panel\" class=\"panel hidden\"><div id=\"files-wrap\"><div id=\"file-tree\"></div><div id=\"file-content\"></div></div></div>\n"
        "<div id=\"replay-panel\" class=\"panel hidden\"><div id=\"replay-wrap\"><div id=\"replay-sidebar\"></div><div id=\"replay-main\"></div></div></div>\n"
        "<div id=\"events-panel\" class=\"panel hidden\"><div id=\"events-wrap\"></div></div>\n"
        "<script type=\"application/json\" id=\"trace-data\">" + json_text + "</script>\n"
        "<script>" + _JS + "</script>\n</body></html>\n"
    )
    out = Path(out_path) if out_path else Path(trace_dir) / "trace.html"
    out.write_text(html, encoding="utf-8")
    return str(out)


def main() -> None:
    import sys
    if len(sys.argv) < 2:
        print("usage: python -m agentic.trace.render <trace_dir> [out.html]")
        sys.exit(1)
    trace_dir = sys.argv[1]
    out = sys.argv[2] if len(sys.argv) > 2 else None
    path = render_trace_html(trace_dir, out)
    print(f"wrote {path}")


if __name__ == "__main__":
    main()
