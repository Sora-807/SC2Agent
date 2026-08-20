# web —— sc2Agent 前端

计划见 `../docs/plan-frontend.md`；帧契约见其 §2（**唯一真相源**）。

- `src/contract/` —— §2 契约的 zod schema（类型由 schema 推导，不手写第二份类型）
- `src/source/` —— 帧源：fixture(F0) / jsonl(F1) / ws(F8) + 环形缓冲
- `fixtures/` —— 帧夹具（F0 由 `pnpm gen:fixtures` 生成；B0 后改由后端 `tools/make_fixtures.py` 产出）

## 命令

```
pnpm install
pnpm gen:fixtures     # 生成 web/fixtures/*.jsonl 并逐帧过 zod 校验
pnpm test             # 契约与夹具测试
pnpm dev              # 起开发服（F0 只有夹具查看器）
```

## 红线（摘自 plan-frontend.md §9）

1. 组件只吃 `contract/` 类型；帧里没有的字段不许现算。
2. 零规则复算：footprint/滞回/阻塞原因/警报/分组/聚类一律后端给。
3. 位置可插值；进度条与计数器绝不插值。
4. zh 文案来自后端数据，前端无 i18n 字典。
5. 任何组件不得假设帧源是 live；不得 import FrameSource 实现（只经 store）。
