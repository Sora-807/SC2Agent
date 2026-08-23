"""view.strategies：策略文件存储（二十七轮用户拍板「开放写策略，免审」）。

策略从此是**文件**：`runtime/strategies/<id>.yaml`，一份文件两段（与
docs/data/tank_marine_push.yaml 同形）：

```yaml
strategy:
  id: tank_marine_push
  display_name_zh: 步坦蛙跳推进
  ...
assembly:
  id: tank_marine_assembly
  groups: [...]
```

此前策略/装配是 api.session 里两个写死的常量（DEFAULT_STRATEGY/DEFAULT_ASSEMBLY）
—— 没有 store、没有文件、UI 和 agent 都改不了（ISSUES I12 留档的「路径上够不着」）。
本 store 把它变成与规划/地图规划同级的第三类 authoring 文件：

- 校验在保存时（parse_strategy/parse_assembly 全套编译期校验），错误带定位返回；
- 会话启动时按 id 装配（`/api/session/start?strategy=<id>`），热改不存在 ——
  正在跑的策略不受影响，新会话用新文件（这是"免审"的安全边界，不是审批）；
- `default` 从内置常量播种（锁定，参考基准）—— 与默认规划/默认地图规划同一姿态。

zh 文案（display_name_zh/reasons 等）在 YAML 里由作者写，不经本模块翻译。
"""
from __future__ import annotations

import re
import threading
import time
from pathlib import Path

import yaml

from flow.manifest import parse_assembly, parse_strategy

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
DEFAULT_STRATEGY_ID = "default"


class StrategyStore:
    """策略文件存储：`{dir}/{id}.yaml`；dir=None = 纯内存（测试）。"""

    def __init__(self, dir: Path | None, *, seed: tuple[str, str] | None = None) -> None:  # noqa: A002
        self._dir = dir
        self._lock = threading.Lock()
        #: id -> {"doc": 原始 YAML dict, "locked": bool, "updated_at": float}
        self._items: dict[str, dict] = {}
        if dir is not None:
            dir.mkdir(parents=True, exist_ok=True)
            for p in sorted(dir.glob("*.yaml")):
                item = self._read(p)
                if item is not None:
                    self._items[item["id"]] = item
        if DEFAULT_STRATEGY_ID not in self._items:
            if seed is None:
                raise ValueError(
                    "StrategyStore 需要种子（default 策略从内置常量播种，"
                    "见 api.app.create_app）")
            doc = {"strategy": yaml.safe_load(seed[0]) or {},
                   "assembly": yaml.safe_load(seed[1]) or {}}
            self._items[DEFAULT_STRATEGY_ID] = {
                "id": DEFAULT_STRATEGY_ID, "doc": doc, "locked": True,
                "updated_at": time.time()}
            self._write(DEFAULT_STRATEGY_ID)

    # ---- 读 ----

    def list(self) -> list[dict]:
        with self._lock:
            return [self._row(sid, it) for sid, it in sorted(self._items.items())]

    @staticmethod
    def _row(sid: str, it: dict) -> dict:
        s = it["doc"].get("strategy") or {}
        return {
            "id": sid,
            "title_zh": str(s.get("display_name_zh") or sid),
            "strategy_id": str(s.get("id") or sid),
            "locked": it["locked"],
            "updated_at": it["updated_at"],
        }

    def doc(self, sid: str) -> dict:
        with self._lock:
            it = self._items.get(sid)
            if it is None:
                raise KeyError(sid)
            return {"id": sid, "locked": it["locked"],
                    **yaml.safe_load(yaml.safe_dump(it["doc"], allow_unicode=True))}

    def file_path(self, sid: str) -> Path | None:
        """会话装配用的真文件路径；内存态（测试）或不存在 = None。"""
        if self._dir is None:
            return None
        p = self._dir / f"{sid}.yaml"
        return p if p.is_file() else None

    # ---- 写 ----

    def create(self, body: dict) -> dict:
        with self._lock:
            sid = str(body.get("id") or "")
            if not _ID_RE.match(sid):
                raise ValueError(f"策略 id {sid!r} 不合法（小写字母/数字/-/_）")
            if sid in self._items:
                raise ValueError(f"策略 id {sid!r} 已存在")
            src = self._items.get(str(body.get("copy_from") or "")) \
                if body.get("copy_from") else None
            if body.get("copy_from") and src is None:
                raise ValueError(f"要复制的策略 {body.get('copy_from')!r} 不存在")
            doc = src["doc"] if src else {"strategy": {}, "assembly": {}}
            if src is not None:   # 复制出来的内层名跟着改，避免转储里两套名
                doc = yaml.safe_load(yaml.safe_dump(doc, allow_unicode=True))
                doc.setdefault("strategy", {})["id"] = sid
                for inst in (doc.get("assembly", {}).get("strategy_instances") or []):
                    inst["strategy_ref"] = sid
            self._items[sid] = {"id": sid, "doc": doc, "locked": False,
                                "updated_at": time.time()}
            self._write(sid)
            # 注意：这里不能调 self.list() —— 当前线程还持着 self._lock，
            # threading.Lock 不可重入（实测 POST /api/strategies 死锁就是这来的）
            return self._row(sid, self._items[sid])

    def save_doc(self, sid: str, doc: dict) -> dict:
        """全量保存 + 编译期校验。返回 {ok, errors: [{text_zh}]}（map-plans 同形）。"""
        with self._lock:
            it = self._items.get(sid)
            if it is None:
                raise KeyError(sid)
            if it["locked"]:
                raise ValueError(f"策略 {sid!r} 已锁定（内置参考基准）：复制一份再改")
            errors: list[str] = []
            strategy = doc.get("strategy")
            assembly = doc.get("assembly")
            if not isinstance(strategy, dict) or not strategy:
                errors.append("文档必须有非空 strategy 段（id/steps/edges）")
            if not isinstance(assembly, dict) or not assembly:
                errors.append("文档必须有非空 assembly 段（groups/strategy_instances）")
            if not errors:
                errors += _compile_errors(sid, strategy, assembly)
            if errors:
                return {"ok": False, "errors": [{"text_zh": e} for e in errors]}
            it["doc"] = {"strategy": strategy, "assembly": assembly}
            it["updated_at"] = time.time()
            self._write(sid)
            return {"ok": True, "errors": []}

    def remove(self, sid: str) -> None:
        with self._lock:
            it = self._items.get(sid)
            if it is None:
                raise KeyError(sid)
            if it["locked"]:
                raise ValueError(f"策略 {sid!r} 已锁定，不能删除（内置参考基准）")
            del self._items[sid]
            if self._dir is not None:
                (self._dir / f"{sid}.yaml").unlink(missing_ok=True)

    # ---- 内部 ----

    def _read(self, path: Path) -> dict | None:
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError):
            return None
        sid = path.stem
        if not isinstance(raw, dict):
            return None
        # 文件里的 id/locked/updated_at 是存储元数据，不进 doc（doc 只有两段正文）
        return {"id": sid,
                "doc": {"strategy": raw.get("strategy") or {},
                        "assembly": raw.get("assembly") or {}},
                "locked": (sid == DEFAULT_STRATEGY_ID),
                "updated_at": path.stat().st_mtime}

    def _write(self, sid: str) -> None:
        if self._dir is None:
            return
        it = self._items[sid]
        data = {"id": sid, "locked": it["locked"], "updated_at": it["updated_at"],
                **it["doc"]}
        path = self._dir / f"{sid}.yaml"
        tmp = path.with_suffix(".tmp")
        tmp.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
                       encoding="utf-8")
        tmp.replace(path)


def _compile_errors(sid: str, strategy: dict, assembly: dict) -> list[str]:
    """parse_strategy/parse_assembly 全套编译期校验；StrategyManifest 的报错本来
    就带 step/branch 定位（中文），原样透传。strategy.id 与文件名不要求一致
    （文件名是 authoring 键，内层 id 是运行时标识）—— 但 strategy_instances 的
    strategy_ref 必须对上内层 id，validate_assembly 会抓。"""
    import copy

    try:
        m = parse_strategy(yaml.safe_dump(copy.deepcopy(strategy), allow_unicode=True))
    except (AssertionError, KeyError, TypeError, yaml.YAMLError) as exc:
        return [str(exc)]
    try:
        a = parse_assembly(yaml.safe_dump(copy.deepcopy(assembly), allow_unicode=True))
    except (AssertionError, KeyError, TypeError, yaml.YAMLError) as exc:
        return [str(exc)]
    try:
        from flow.manifest import validate_assembly

        validate_assembly(m, a)
    except AssertionError as exc:
        return [str(exc)]
    return []


def load_strategy_file(path: Path) -> tuple:
    """策略文件 → (StrategyManifest, FlowAssembly)。会话装配（Offline/run_session）用；
    坏文件/编译失败原样抛（调用方 400 带原因 —— 会话起不来要说清楚是策略的问题）。"""
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    strategy = raw.get("strategy") or raw          # 允许裸 strategy 形态（没套两层）
    assembly = raw.get("assembly") or {}
    if not assembly:
        raise ValueError(f"{path.name} 缺 assembly 段（groups/strategy_instances）")
    return parse_strategy(yaml.safe_dump(strategy, allow_unicode=True)), \
        parse_assembly(yaml.safe_dump(assembly, allow_unicode=True))
