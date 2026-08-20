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

from api.app import DEFAULT_FRAME_DIR, create_app  # noqa: E402


def main() -> int:
    import uvicorn

    ap = argparse.ArgumentParser(description="sc2Agent view API")
    ap.add_argument("--frame-dir", default=str(ROOT / DEFAULT_FRAME_DIR))
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8770)
    ap.add_argument("--log-level", default="warning")
    args = ap.parse_args()
    print(f"view API → http://{args.host}:{args.port}/api/health  帧源目录 {args.frame_dir}")
    uvicorn.run(create_app(args.frame_dir), host=args.host, port=args.port,
                log_level=args.log_level)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
