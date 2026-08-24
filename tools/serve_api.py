"""tools/serve_api.py：起 view API（开发用）。

为什么需要这个壳：`modules/` 只在 pytest 的 `pythonpath` 配置里，直接
`python -m api.app` 找不到包。这里把源根塞进 `sys.path` 再交给 uvicorn。

用法：
    uv run python tools/serve_api.py                        # 默认 127.0.0.1:8770，读 web/public/fixtures
    uv run python tools/serve_api.py --frame-dir traces/x   # 换帧源目录（复盘录制）
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "modules"))
# agent 对话服务要用根下的 agent/ 包与 vendor 的 agentic 运行时（P3 切片 A）
sys.path.insert(0, str(ROOT / "vendor"))
sys.path.insert(0, str(ROOT))

from api.app import (DEFAULT_FRAME_DIR, DEFAULT_MAP_PLANS_DIR, DEFAULT_PLANS_DIR,
                     DEFAULT_PROPOSAL_LOG, DEFAULT_RECORDINGS_DIR, create_app)  # noqa: E402


# ---------------- 端口守卫（2026-08-24：stale serve_api 反复锁死 8770，用户拍板自动清）----------------


def listening_on(conns, host: str, port: int) -> list[tuple[int, str, str]]:
    """(ip, port, status, pid, name) 序列 → 监听在本 (host, port) 上的 [(pid, name, 命令行)]。

    纯函数（psutil 的结果喂进来），匹配规则：
    - status 必须是 LISTEN；
    - 端口相等；地址 = host 本身，或通配（0.0.0.0/::——通配监听会和具体 IP 绑定冲突）；
      host 自身是通配时，任何地址的监听都算冲突。
    """
    wildcard = host in ("0.0.0.0", "::")
    out = []
    for c in conns:
        ip, cport, status, pid, name, cmdline = c
        if status != "LISTEN" or cport != port or pid is None:
            continue
        if not wildcard and ip not in (host, "0.0.0.0", "::"):
            continue
        out.append((pid, name or "", cmdline or ""))
    return out


def looks_like_our_backend(cmdline: str) -> bool:
    """命令行像不像「我们自己的后端」（serve_api / uvicorn）—— 只自动清自己的，陌生进程不误杀。"""
    c = (cmdline or "").lower()
    return "serve_api" in c or "uvicorn" in c


def _psutil_conns():
    """psutil 的连接表 → listening_on 要的六元组；无 psutil / 无权限 → []（降级不炸）。"""
    try:
        import psutil
    except ImportError:
        return []
    out = []
    try:
        conns = psutil.net_connections(kind="tcp")
    except Exception:                     # noqa: BLE001 —— 权限/平台差异：拿到多少算多少
        return []
    for c in conns:
        try:
            ip = c.laddr.ip if c.laddr else ""
            cmdline = " ".join(psutil.Process(c.pid).cmdline()) if c.pid else ""
            name = psutil.Process(c.pid).name() if c.pid else ""
        except Exception:                 # noqa: BLE001 —— 进程已退/无权限
            ip, cmdline, name = ip if c.laddr else "", "", ""
        out.append((ip, c.laddr.port if c.laddr else 0, c.status, c.pid, name, cmdline))
    return out


def ensure_port_free(host: str, port: int, *, auto_kill: bool = True,
                     timeout: float = 10.0, conns=None, sleep=time.sleep) -> bool:
    """起服务前确认 (host, port) 可绑：上一代后端自动树杀（含 run_session/SC2 子进程），
    陌生占用不误杀 —— 打印 PID 与处理指引后返回 False。返回 True = 可以绑了。"""
    holders = listening_on(_psutil_conns() if conns is None else conns, host, port)
    if not holders:
        return True
    import psutil  # 上面的降级路径没 psutil 时，这里如实抛（清端口没它干不了）

    killed = False
    for pid, name, cmdline in holders:
        desc = f"PID {pid}（{name}{' · ' + cmdline[:100] if cmdline else ''}）"
        if not auto_kill or not looks_like_our_backend(cmdline):
            print(f"[端口守卫] {host}:{port} 被 {desc} 占着，且不像本项目的后端 —— 不敢自动杀。"
                  f"手动处理：taskkill /PID {pid} /T /F（Windows）/ kill {pid}（POSIX）",
                  file=sys.stderr)
            return False
        try:
            proc = psutil.Process(pid)
            kids = proc.children(recursive=True)   # 树杀：stale 后端的 run_session/SC2 一起收
            for k in kids:
                k.kill()
            proc.kill()
            for k in kids:
                k.wait(timeout=3)
            proc.wait(timeout=3)
            print(f"[端口守卫] {host}:{port} 被上一代后端 {desc} 占着 → 已结束（含 {len(kids)} 个子进程）")
            killed = True
        except psutil.NoSuchProcess:
            pass    # 恰好自己退了
        except psutil.Error as exc:
            print(f"[端口守卫] 结束 {desc} 失败：{exc} —— 手动处理：taskkill /PID {pid} /T /F",
                  file=sys.stderr)
            return False
    if not killed:
        return False
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not listening_on(_psutil_conns(), host, port):
            return True
        sleep(0.3)
    print(f"[端口守卫] 旧进程已杀但 {host}:{port} 还被占着（socket 释放慢）—— 稍后再试",
          file=sys.stderr)
    return False


def main() -> int:
    import uvicorn

    ap = argparse.ArgumentParser(description="sc2Agent view API")
    ap.add_argument("--frame-dir", default=str(ROOT / DEFAULT_FRAME_DIR))
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8770)
    ap.add_argument("--proposals", default=str(ROOT / DEFAULT_PROPOSAL_LOG))
    ap.add_argument("--plans", default=str(ROOT / DEFAULT_PLANS_DIR),
                    help="规划文件目录（一个规划一个 YAML）")
    ap.add_argument("--map-plans", default=str(ROOT / DEFAULT_MAP_PLANS_DIR),
                    help="地图规划文件目录（默认地图锁定 + 复制新建）")
    ap.add_argument("--recordings", default=str(ROOT / DEFAULT_RECORDINGS_DIR),
                    help="对局记录目录（live 帧流落盘；复盘下拉里的对局记录）")
    ap.add_argument("--loadouts", default=str(ROOT / "runtime/loadouts"),
                    help="装配清单目录（loadout：三件套引用，只读）")
    ap.add_argument("--strategies", default=str(ROOT / "runtime/strategies"),
                    help="策略文件目录（开放写策略：strategy+assembly 两段 YAML）")
    ap.add_argument("--log-level", default="warning")
    ap.add_argument("--no-kill", action="store_true",
                    help="端口被上一代后端占用时不自动清理（默认自动树杀后重启）")
    args = ap.parse_args()
    if not ensure_port_free(args.host, args.port, auto_kill=not args.no_kill):
        return 1
    print(f"view API → http://{args.host}:{args.port}/api/health")
    print(f"  帧源目录 {args.frame_dir}")
    print(f"  提案日志 {args.proposals}")
    print(f"  规划目录 {args.plans}")
    print(f"  地图规划目录 {args.map_plans}")
    print(f"  对局记录目录 {args.recordings}")
    print(f"  agent 对话 → POST http://{args.host}:{args.port}/api/agent/chat"
          "（LLM 读 .env，未配密钥时首条消息会说明）")
    uvicorn.run(create_app(args.frame_dir, args.proposals, args.plans, args.map_plans,
                           recordings_dir=args.recordings,
                           strategies_dir=args.strategies,
                           loadouts_dir=args.loadouts,
                           agent_base=f"http://{args.host}:{args.port}"),
                host=args.host, port=args.port, log_level=args.log_level)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
