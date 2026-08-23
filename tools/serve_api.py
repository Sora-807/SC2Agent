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
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "modules"))
# agent 对话服务要用根下的 agent/ 包与 vendor 的 agentic 运行时（P3 切片 A）
sys.path.insert(0, str(ROOT / "vendor"))
sys.path.insert(0, str(ROOT))

from api.app import (DEFAULT_FRAME_DIR, DEFAULT_MAP_PLANS_DIR, DEFAULT_PLANS_DIR,
                     DEFAULT_PROPOSAL_LOG, DEFAULT_RECORDINGS_DIR, create_app)  # noqa: E402


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
    ap.add_argument("--strategies", default=str(ROOT / "runtime/strategies"),
                    help="策略文件目录（开放写策略：strategy+assembly 两段 YAML）")
    ap.add_argument("--log-level", default="warning")
    args = ap.parse_args()
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
                           agent_base=f"http://{args.host}:{args.port}"),
                host=args.host, port=args.port, log_level=args.log_level)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
