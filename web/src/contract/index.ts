/**
 * ViewFrame v0.1 —— 帧契约（zod-first）
 *
 * 唯一真相源：`docs/contract/plan-frontend.md` §2。本文件是它的可执行镜像。
 * 类型一律由 schema 推导（`z.infer`），**不手写第二份 interface**，否则类型与校验会漂移。
 * 改动流程：改字段 = REV+1 + §2 修订 + 后端 contract test 同步 + 两份计划都改（红线 C8）。
 */
import { z } from "zod";

/**
 * 契约版本。
 * rev 13：`world.units[].producing[].progress` 收窄为可空 —— SC2 订单不带进度
 *   （协议没有该字段），后端原先恒发 `0.0` 是把「未知」伪装成「刚开始」，改为 null。
 * rev 10：B12+B13 一轮两字段（F11 地图视觉语言需要）：
 *   ① `frame/economy.nodes[]` 增 `base_tag`（节点归属基地 = 最近的己方 dropoff 建筑 tag）——
 *     与 frame/world.units 按 tag 直接 join，主基地标签写「矿 12/16 气 3/6」不需要空间匹配；
 *   ② `static/catalog.entries[]` 增 `short_name_zh`（≤2 字短名）—— footprint 内标签与
 *     单位聚类 chip 的字形。后端加字段而不是前端截断（PLAN §12 结论 2：U6/C4）。
 * rev 9：新增 topic `static/terrain`（B4）。它是**事件式静态面**：
 *   `static/map` 先到（terrain=null），game_info 就绪后地形帧补到 ——
 *   前端把它的 payload **合并进 map.terrain**，而不是等一张完整的 static/map。
 *   真机上 game_info 在 bot 第一个 on_step 才可用，static/map 等不起。
 * rev 8：`frame/projection` 增 `skipped`；`source.kind="live_queue"` 终于有真值。
 *   之前 `Planner` 与 `ProductionRuntime` 没有互转（一个吃 ProductionModuleInstance、
 *   一个执行 QueueItem），所以"当前队列的投影"产不出来，UI 只能诚实地写「参考计划」。
 *   后端补了 `view.projection` 这条桥，现在能投真队列了 —— UI 的措辞跟着改。
 *   `skipped` 是"不静默"：队列里有项投不了（如 cancel）必须让用户看见。
 * rev 7：新增 topic `static/strategy` —— 策略图结构（steps/branches/edges/声明节）。
 *   `frame/flow` 只有"现在在哪个 step"，**图本身不在任何帧里**，F4 的状态图与 F9 的 AST 编辑器
 *   都没法画。它每个 flow 版本只变一次，所以归静态面；hot-edit（S8）落地后改成事件驱动。
 *   `branches` **原样带值树**（不摊平）：F4 只要 step/edge，但 F9 的编辑器要完整 AST。
 * rev 6：新增 topic `frame/economy`（ADR-0030 的经济维持器已落地）。
 *   `tasks` 里 **quota / target / actual 三个数都给**：quota 是持久配额（没精炼厂也挂着 ——
 *   这正是 issues P9 的修复点），target 是本帧可达（被节点容量与人数夹紧），actual 是真实人数。
 *   只给 target 的话用户会以为意图又蒸发了，恰好与修复相反。
 * rev 5：`forbidden` 定为**开放分组表**（`Record<组名, Record<op, 原因>>`）而不是固定三组。
 *   后端刚给它加了 `composite_actions`（assign_workers 需要扇出层，ADR-0030 D1）与 `step_keys`
 *   两组；写成封闭对象时 zod 会**静默 strip** 掉它们，编辑器就会以为那些 op 可用。
 *   这才是 rev 2"逐字镜像、不加工"该有的样子：连分组名都不枚举。
 * rev 4（B1 落地）：按后端**实际能产出什么**校准三处 ——
 *   ① `frame/flow` 增 `eval_diagnostics`（引擎本来就在记"条件其实没求出来"，不暴露等于丢功能）；
 *   ② `items[].status` 收窄为 队首阻塞/未处理 并删 `resolved_point`
 *      —— 队首门控语义下，已发出的项已出队或进了 `in_flight`，队列里不可能有"已发出"；
 *   ③ `in_flight[]` 删 `timeout_frames`/`confirmed`（前者要在前端复制一份魔法公式、后者恒 false），
 *      改增 `queue` + `attempted_slots`（摆放调试叠加要画"试过哪几个槽位"）。
 * rev 3：区域几何改为"一张标签网格 + 索引"。原来每个 leaf 带一份 mask 不可扩展 ——
 *   LadderMap 176×160=28160 格，一份 mask ≈37KB；20 个区域按 mask 发就是 750KB，
 *   按标签网格发始终 37KB。同时这也直接镜像后端 `tactical_map.RegionLayer` 的模型，
 *   前端画区域变成"一张位图 + 一份调色板"。
 * rev 2（DSL v0.2 落地后）：`static/schema` 改为直接镜像 `modules/flow/vocab.py::dump_vocabulary()`
 *   —— 那张表同时喂校验器、LLM 提示词卡片、可视化编辑器，是唯一权威源；
 *   顺带修掉 rev 1 手抄词表时的实际错误（follow/research/use_ability 的参数、point_toward 的 origin）。
 *   `frame/production` 增队列级 `blocked`（后端 T4 已有 `runtime.blocked`：原因 + 起始时间 + 是否已告警）。
 * rev 1：初版（DSL v0.2 之前，签名表尚不存在，schema 降级为空参数表）。
 * rev 10：B12/B13 —— economy.nodes[] 增 base_tag；catalog 增 short_name_zh。
 * rev 11：B14 —— 提案 hunks.kind 增 map_plan 六操作；preview.map_overlay 增 changed_marks。
 * rev 12：策略可读性（I1/I2/I4）—— schema 的 predicates/operators/actions 每项加 name_zh；
 *   strategy 增 display_name_zh/description_zh（策略级与 step 级）、reasons、group_names。
 *   新字段全部 `.default()` 容错：旧夹具/旧缓存帧缺字段时退回 identifier，不炸整页。
 */
export const REV = 13 as const;

/* ---------------- 基础类型 ---------------- */

/** 世界坐标（左下原点浮点） */
export const zPt = z.tuple([z.number(), z.number()]);
/** 建筑格点 */
export const zCell = z.tuple([z.number(), z.number()]);
/** 栅格：行主序 data[y][x]，base64 的 uint8。禁发嵌套数组（红线 C5） */
export const zGridB64 = z.object({
  w: z.number().int().nonnegative(),
  h: z.number().int().nonnegative(),
  bpp: z.literal(8),
  data_b64: z.string(),
});

export const zTopic = z.enum([
  "static/map",
  "static/catalog",
  "static/schema",
  "static/strategy",
  "static/terrain",
  "frame/session",
  "frame/world",
  "frame/flow",
  "frame/production",
  "frame/economy",
  "frame/ops",
  "frame/projection",
  "frame/alerts",
  "proposals",
]);
export type Topic = z.infer<typeof zTopic>;

/**
 * 静态面：每局只发一次，任何游标下都有效。
 * **不参与"可 seek 窗口"的计算** —— 否则环形缓冲淘汰了早期动态帧之后，
 * 静态帧仍会把窗口左端钉在 t=0，时间线就会假装能拖回开局（拿到的却是窗口内最老的动态帧）。
 */
export const STATIC_TOPICS = [
  "static/map", "static/catalog", "static/schema", "static/strategy", "static/terrain",
] as const;
export type StaticTopic = (typeof STATIC_TOPICS)[number];

const STATIC_SET: ReadonlySet<string> = new Set<string>(STATIC_TOPICS);
export const isStaticTopic = (t: Topic): boolean => STATIC_SET.has(t);

export const zOwner = z.enum(["self", "ally", "enemy", "neutral"]);
export const zRole = z.enum(["worker", "combat", "building", "upgrade"]);
export const zSlotKind = z.enum(["supply", "production", "addon"]);

/* ---------------- 静态面 ---------------- */

export const zMapStatic = z.object({
  map_name: z.string(),
  size: z.tuple([z.number().int(), z.number().int()]),
  spawn: z.string(),
  /** B4 之前为 null：前端降级为纯色底 */
  terrain: z
    .object({
      height: zGridB64.nullable(),
      pathable: zGridB64.nullable(),
      placeable: zGridB64.nullable(),
    })
    .nullable(),
  /**
   * 区域几何 = 一张标签网格 + 索引（不是每区一份 mask，见 REV 3 说明）。
   * 索引键在 JSON 里是字符串（网格里存 int 标签）：`big_index["1"] === "main"`。
   */
  regions: z.object({
    big: z.array(
      z.object({
        stable_id: z.string(),
        anchor: zPt,
        display_name_zh: z.string(),
        aliases_zh: z.array(z.string()),
        children: z.array(z.string()),
      }),
    ),
    leaf: z.array(
      z.object({
        stable_id: z.string(),
        parent: z.string(),
        anchor: zPt,
        display_name_zh: z.string(),
        aliases_zh: z.array(z.string()),
        build_slots: z.array(z.string()),
      }),
    ),
    big_grid: zGridB64.nullable(),
    leaf_grid: zGridB64.nullable(),
    big_index: z.record(z.string(), z.string()),
    leaf_index: z.record(z.string(), z.string()),
  }),
  /** br/build_point/reported_position 由后端按 ADR-0027 算好；前端零几何换算（红线 C2） */
  build_slots: z.array(
    z.object({
      name: z.string(),
      tl: zCell,
      br: zCell,
      size: z.number().int(),
      kind: zSlotKind,
      build_point: zPt,
      reported_position: zPt,
    }),
  ),
  pos_marks: z.array(
    z.object({ name: z.string(), pos: zPt, description_zh: z.string() }),
  ),
  resource_nodes: z.array(
    z.object({
      tag: z.number().int(),
      stable_id: z.string(),
      pos: zPt,
      kind: z.enum(["mineral", "geyser"]),
    }),
  ),
});

export const zCatalogStatic = z.object({
  entries: z.array(
    z.object({
      stable_id: z.string(),
      display_name_zh: z.string(),
      /** ≤2 字短名（B13）：地图 footprint 标签与聚类 chip 字形 */
      short_name_zh: z.string(),
      role: zRole,
      capabilities: z.array(z.string()),
      cost: z.object({
        minerals: z.number().int(),
        vespene: z.number().int(),
        supply: z.number().int(),
      }),
      build_time: z.number(),
      produced_by: z.string().nullable(),
      prerequisites: z.array(z.string()),
      /** footprint 格边长（建筑）；非建筑 null */
      size: z.number().int().nullable(),
      attack_range: z.number().nullable(),
      siege_range: z.number().nullable(),
      /** 仅允许调试面板显示；语义一律用 stable_id（红线 C1） */
      burnysc2_name: z.string(),
    }),
  ),
});

/**
 * AST 编辑器（F9）与 Flow 状态图（F4）的唯一 schema 源。
 *
 * 前 8 个字段**逐字镜像** `modules/flow/vocab.py::dump_vocabulary()` —— 后端已经把它做成
 * "校验器 / LLM 提示词卡片 / 可视化编辑器目录"共享的权威表，所以前端只需照搬，绝不手抄
 * （rev 1 手抄的代价：follow/research/use_ability 的参数、point_toward 的 origin 全错）。
 * 最后两个字段是生产与目标解析侧的闭集，不属 flow.vocab，由后端另行导出。
 */
export const zSchemaStatic = z.object({
  /** 谓词/空间工具签名。`kind`：bool = 可作 when 条件；value = 可放在参数位（如 group_center） */
  predicates: z.record(
    z.string(),
    z.object({
      /** 中文名（rev 12，I1）：UI 条件 chip 与提示词共用，真相源在 flow.vocab */
      name_zh: z.string().default(""),
      params: z.array(z.object({ name: z.string(), required: z.boolean() })),
      kind: z.enum(["value", "bool"]),
    }),
  ),
  /** 比较/逻辑运算符 arity；max_args=null 表示不限（and/or） */
  operators: z.record(
    z.string(),
    z.object({
      name_zh: z.string().default(""),
      min_args: z.number().int(),
      max_args: z.number().int().nullable(),
    }),
  ),
  /** group_action.action_atom 目录（= game.operation.OP_CATALOG，含 ParamType） */
  actions: z.record(
    z.string(),
    z.object({
      name_zh: z.string().default(""),
      params: z.array(
        z.object({ name: z.string(), type: z.string(), required: z.boolean() }),
      ),
    }),
  ),
  do_ops: z.array(z.string()),
  /**
   * 不可用的东西：`{组名: {op: 原因}}`。编辑器置灰并显示**后端给的原因**（红线 C3/C4）。
   * **分组名刻意不枚举**：后端目前有 predicates / spatial_tools / do_ops / composite_actions /
   * step_keys 五组，以后还会加。写成封闭对象 = 新分组被 zod 静默 strip = 编辑器以为那些 op 可用。
   */
  forbidden: z.record(z.string(), z.record(z.string(), z.string())),
  /** 声明节白名单：params 的键与类型、loop_limits 的键 */
  declarations: z.object({
    param_keys: z.array(z.string()),
    param_types: z.array(z.string()),
    loop_limit_keys: z.array(z.string()),
  }),
  /** 各节点形态的示例写法（编辑器提示 + 新手引导直接用） */
  node_forms: z.record(z.string(), z.string()),
  /** 编译规则的人类可读清单（编辑器侧栏 / 校验失败时的解释） */
  rules: z.array(z.string()),

  /** 生产队列侧闭集（来自 game.production + production.runtime） */
  queue: z.object({
    ops: z.array(z.string()),
    /** op -> 不支持的原因（不静默：UI 置灰并解释） */
    unsupported_ops: z.record(z.string(), z.string()),
    worker_tasks: z.array(z.string()),
  }),
  /** 目标解析类型（point/region/group_center/nearest_enemy） */
  target_kinds: z.array(z.string()),
});

/* ---------------- 动态面 ---------------- */

/**
 * 策略图结构（每个 flow 版本只变一次）。
 * 与 `frame/flow` 的分工：这里是**图**（不变的结构），那里是**状态**（每帧的位置）。
 */
/** 地形三图（B4）。任一可为 null：game_info 里三张图的可用性不保证一致，缺哪张画哪张 */
export const zTerrainFrame = z.object({
  height: zGridB64.nullable(),
  pathable: zGridB64.nullable(),
  placeable: zGridB64.nullable(),
});

export const zStrategyStatic = z.object({
  id: z.string(),
  version: z.number().int(),
  /** 中文名与意图描述（rev 12，I2）："" = 没写，UI 退回 identifier */
  display_name_zh: z.string().default(""),
  description_zh: z.string().default(""),
  group_slots: z.array(z.string()),
  /** 参数/变量的**声明**（type + default + description_zh?）；生效值在 frame/flow */
  params: z.record(z.string(), z.record(z.string(), z.unknown())),
  variables: z.record(z.string(), z.record(z.string(), z.unknown())),
  definitions: z.record(z.string(), z.unknown()),
  initial_step: z.string(),
  steps: z.array(
    z.object({
      step_id: z.string(),
      display_name_zh: z.string().default(""),
      description_zh: z.string().default(""),
      /** 原样的值树：when 的 AST 与 do 的动作列表都不摊平（F9 的编辑器要用） */
      branches: z.array(z.record(z.string(), z.unknown())),
    }),
  ),
  edges: z.array(
    z.object({
      from: z.string(),
      to: z.string(),
      /** (kind, reason) 必须与某个 exit_step 匹配（编译期已校验，没有对应的边是死边） */
      kind: z.string(),
      reason: z.string(),
    }),
  ),
  loop_limits: z.record(z.string(), z.number().int()),
  /** slot → group_id：画图时标注每个 slot 落在哪个组 */
  bindings: z.record(z.string(), z.string()),
  /** reason 标识符 → 中文（FORMED → 成型）：edges 切换原因与 exit 终局原因共用（rev 12） */
  reasons: z.record(z.string(), z.string()).default({}),
  /** group_id → 中文（G_INF → 步兵组），来自 assembly（rev 12） */
  group_names: z.record(z.string(), z.string()).default({}),
});

export const zSessionFrame = z.object({
  state: z.enum(["未连接", "启动中", "对局中", "已结束", "崩溃"]),
  frame_source: z.enum(["live", "replay", "fixture"]),
  map_name: z.string().nullable(),
  my_race: z.string().nullable(),
  enemy_race: z.string().nullable(),
  game_time: z.number(),
  error: z.string().nullable(),
});

export const zWorldFrame = z.object({
  economy: z.object({
    minerals: z.number().int(),
    vespene: z.number().int(),
    supply_used: z.number().int(),
    supply_cap: z.number().int(),
  }),
  units: z.array(
    z.object({
      tag: z.number().int(),
      /** 后端已翻译并归一变体；禁出现 burnysc2 名（红线 C1） */
      stable_id: z.string(),
      /** 形态变体（"sieged"）；null = 主形态 */
      form: z.string().nullable(),
      owner: zOwner,
      pos: zPt,
      facing: z.number(),
      hp: z.number(),
      hp_max: z.number(),
      shield: z.number(),
      energy: z.number(),
      build_progress: z.number(),
      /** flow 分组归属，后端 join；前端不自己算（红线 C3） */
      group_id: z.string().nullable(),
      order: z
        .object({
          /** 映射到 OP_CATALOG 的原子名；映射不出则 null（后端不猜） */
          atom: z.string().nullable(),
          /**
           * SC2 原生能力名，诊断用。**红线 C1 的明确例外**：SC2 里训机枪兵的能力就叫 "Marine"，
           * 这是能力名不是类型身份。类型身份一律看 `stable_id`。
           */
          ability_raw: z.string(),
          target_tag: z.number().int().nullable(),
          target_pos: zPt.nullable(),
          is_auto: z.boolean(),
        })
        .nullable(),
      /** 建筑 footprint 矩形；后端算好，前端只画（红线 C2） */
      footprint: z.object({ tl: zCell, br: zCell }).nullable(),
      producing: z
        .array(z.object({ stable_id: z.string(), progress: z.number().nullable() }))
        .nullable(),
      addon: z.enum(["reactor", "techlab"]).nullable(),
      carrying: z.enum(["minerals", "vespene"]).nullable(),
      buffs: z.array(z.string()),
    }),
  ),
  /** 聚类未实现时 null → 前端降级为逐个单位显示 */
  enemy_clusters: z
    .array(
      z.object({
        center: zPt,
        radius: z.number(),
        count: z.number().int(),
        by_stable_id: z.record(z.string(), z.number().int()),
      }),
    )
    .nullable(),
  resource_state: z.array(
    z.object({
      tag: z.number().int(),
      remaining: z.number().nullable(),
      workers: z.number().int(),
    }),
  ),
  /** 仅变化时下发 */
  grids: z
    .object({ creep: zGridB64.optional(), visibility: zGridB64.optional() })
    .nullable(),
});

export const zFlowFrame = z.object({
  /** V1 恒长度 1；列表形状为多实例预留（红线 C6） */
  strategies: z.array(
    z.object({
      instance_id: z.string(),
      strategy_ref: z.string(),
      version: z.number().int(),
      params: z.record(z.string(), z.unknown()),
      variables: z.record(z.string(), z.unknown()),
      locals: z.record(z.string(), z.unknown()),
      definitions: z.record(z.string(), z.unknown()),
      active_step: z.string(),
      step_entered_at: z.number(),
      step_elapsed: z.number(),
      step_entry_count: z.number().int(),
      branch_hit: z
        .object({
          step_id: z.string(),
          branch_id: z.string().nullable(),
          index: z.number().int(),
        })
        .nullable(),
      /** 最近 K=20 条 */
      transitions: z.array(
        z.object({
          from: z.string(),
          to: z.string(),
          kind: z.string().nullable(),
          reason: z.string().nullable(),
          at: z.number(),
        }),
      ),
      transition_count: z.number().int(),
      transition_limit: z.number().int(),
      done: z.boolean(),
      exit_record: z
        .object({ kind: z.string(), reason: z.string() })
        .nullable(),
      bindings: z.record(z.string(), z.string()),
      /**
       * 求值诊断：比较遇 None 降级为 False 等路径的留痕（引擎 H6）。
       * 引擎注释写着"UI/agent 读它就知道条件其实没求出来" —— 调试页（F5）显示它。
       */
      eval_diagnostics: z.array(
        z.object({
          step_id: z.string(),
          kind: z.string(),
          detail: z.string(),
          count: z.number().int(),
        }),
      ),
    }),
  ),
  groups: z.array(
    z.object({
      group_id: z.string(),
      composition: z.record(
        z.string(),
        z.object({
          min: z.number().int(),
          target: z.number().int(),
          max: z.number().int(),
          current: z.number().int(),
        }),
      ),
      /** 后端按 DSL-T3 滞回语义判定；前端不复算（红线 C3） */
      refill_state: z.enum(["满足", "滞回区", "补兵中", "已截断"]),
      leased_tags: z.array(z.number().int()),
      center: zPt.nullable(),
      hp_ratio: z.number().nullable(),
    }),
  ),
});

export const zProductionFrame = z.object({
  queues: z.array(
    z.object({
      name: z.string(),
      head_status: z.enum(["空", "可执行", "阻塞"]),
      /**
       * 队首阻塞详情（后端 `ProductionRuntime.blocked[name]`）。
       * `warned` = 已持续超过后端的 STALL_WARN_SECS 阈值 —— 阈值判断在后端，前端不复算（红线 C3）。
       */
      blocked: z
        .object({
          reason: z.string(),
          since: z.number(),
          waited: z.number(),
          warned: z.boolean(),
        })
        .nullable(),
      items: z.array(
        z.object({
          index: z.number().int(),
          op: z.enum(["build", "train", "research", "cancel", "assign_workers"]),
          stable_id: z.string().nullable(),
          count: z.number().int(),
          placement: z
            .union([
              z.object({ kind: z.literal("exact"), mark: z.string() }),
              z.object({
                kind: z.literal("in_region"),
                region: z.string(),
                index: z.number().int().nullable(),
              }),
            ])
            .nullable(),
          task: z.enum(["mineral", "gas", "idle"]).nullable(),
          /**
           * 只有两种：队首门控语义下，已发出的项已出队或进了 `in_flight`，
           * 所以队列里不可能出现"已发出/在途"（rev 4 校准）。后端给，前端不推断（红线 C3）。
           */
          status: z.enum(["队首阻塞", "未处理"]),
          block_reason: z.string().nullable(),
        }),
      ),
    }),
  ),
  /**
   * 在途建造确认（真机教训：SC2 对非法放置位静默丢弃命令）。
   * `builder_tag === null` = 上帧失败、本帧待重试；`attempted_slots` 给摆放调试叠加层画"试过哪些位置"。
   */
  in_flight: z.array(
    z.object({
      queue: z.string(),
      stable_id: z.string(),
      kind: z.enum(["build", "addon", "gas"]),
      builder_tag: z.number().int().nullable(),
      expect_pos: zPt.nullable(),
      radius: z.number(),
      frames_waited: z.number().int(),
      retries: z.number().int(),
      attempted_slots: z.array(z.string()),
    }),
  ),
  dropped: z.array(
    z.object({
      /** 后端丢弃 QueueItem 时未记 game_time → null，UI 显示"未知"而不是编时间（不静默） */
      at: z.number().nullable(),
      op: z.string(),
      stable_id: z.string().nullable(),
      reason: z.string(),
    }),
  ),
});

/**
 * 经济维持器的收敛状态（ADR-0030 D4.6）—— "SCV 去哪了、为什么闲置"的唯一解释来源。
 * `emitted_count === 0` 就是"稳定态零命令"（ADR-0030 验收 6）。
 */
export const zEconomyFrame = z.object({
  tasks: z.array(
    z.object({
      task: z.enum(["mineral", "gas", "idle"]),
      /** 持久配额（null = 没设，按 per-node 比例）。语义是"**维持** N 个"，不是"再派 N 个" */
      quota: z.number().int().nullable(),
      /** 本帧可达目标：被节点容量与领地人数夹紧；没精炼厂时 gas 为 0 而 quota 仍在 */
      target: z.number().int(),
      actual: z.number().int(),
    }),
  ),
  nodes: z.array(
    z.object({
      tag: z.number().int(),
      kind: z.enum(["mineral", "gas"]),
      workers: z.number().int(),
      /** 矿 2 / 气 3 —— 后端的真机常数，前端不硬编码 */
      capacity: z.number().int(),
      saturated: z.boolean(),
      /** 归属基地的 unit tag（最近的己方 dropoff 建筑）；无基地 → null（B12） */
      base_tag: z.number().int().nullable(),
    }),
  ),
  /** 建造征用（owner 如 "production/build#3"）：有它，"抢走正在建造的 SCV"才是结构性不可能 */
  reserved: z.array(z.object({ tag: z.number().int(), owner: z.string() })),
  /** 本帧发出的差量条数。维持器只记条数不记内容，所以不是列表 */
  emitted_count: z.number().int(),
  /** 维持器领地大小（没被战斗组租用、也没被征用的工兵数） */
  domain_workers: z.number().int(),
  quotas: z.record(z.string(), z.number().int()),
});

export const zOpsFrame = z.object({
  ops: z.array(
    z.object({
      op_id: z.number().int(),
      seq: z.number().int(),
      at: z.number(),
      action: z.string(),
      unit_tags: z.array(z.number().int()),
      params: z.record(z.string(), z.unknown()),
      /** 谁发的（追责）；由后端 RecordingPort 打标，不给 Operation 加字段 */
      origin: z.enum(["flow", "production", "worker", "user", "agent"]),
      /** B9（D6）之前恒 null → 前端显示"未知"而不是空白 */
      apply: z
        .object({
          /** null = 已受理、待裁决（异步应用未回；「下一 step 生效」语义的如实反映） */
          ok: z.boolean().nullable(),
          failed: z.boolean(),
          detail: z.string().nullable(),
        })
        .nullable(),
      landing: z
        .object({
          kind: z.enum(["confirmed", "timeout", "retried", "unknown"]),
          at: z.number().nullable(),
          detail: z.string().nullable(),
        })
        .nullable(),
    }),
  ),
});

export const zProjectionFrame = z.object({
  based_on_seq: z.number().int(),
  based_on_game_time: z.number(),
  horizon: z.number(),
  source: z.union([
    z.object({ kind: z.literal("live_queue"), queue_name: z.string() }),
    z.object({ kind: z.literal("draft"), plan_id: z.string() }),
  ]),
  points: z.array(
    z.object({
      t: z.number(),
      minerals: z.number(),
      gas: z.number(),
      supply_used: z.number().int(),
      supply_cap: z.number().int(),
      mineral_workers: z.number().int(),
      gas_workers: z.number().int(),
      buildings: z.record(z.string(), z.number().int()),
      units: z.record(z.string(), z.number().int()),
      in_flight_count: z.number().int(),
    }),
  ),
  events: z.array(
    z.object({
      kind: z.enum(["started", "completed", "stalled"]),
      stable_id: z.string().nullable(),
      t: z.number(),
      reason: z.string().nullable(),
    }),
  ),
  /** 队列里投不进来的项 + 原因（如 cancel）。不显示的话投影会悄悄少算一段 */
  skipped: z.array(z.object({ op: z.string(), reason: z.string() })),
});

export const zAlertsFrame = z.object({
  alerts: z.array(
    z.object({
      id: z.string(),
      kind: z.enum([
        "supply_block",
        "mineral_float",
        "gas_float",
        "line_idle",
        "prereq_missing",
        "plan_drift",
        "queue_blocked",
      ]),
      severity: z.enum(["info", "warn", "error"]),
      at: z.number(),
      eta: z.number().nullable(),
      /** 文案由后端生成（红线 C4：前端无 i18n 字典） */
      text_zh: z.string(),
      source: z.enum(["projection", "production", "session"]),
      payload: z.record(z.string(), z.unknown()),
    }),
  ),
});

/** 草稿提案（plan-frontend.md §6）；agent 的唯一产出面 */
export const zProposal = z.object({
  id: z.string(),
  author: z.enum(["agent", "user"]),
  created_at: z.number(),
  /** 提案基于哪一帧；过期即"已失效"，禁止盲接受（P5） */
  anchor: z.object({ seq: z.number().int(), game_time: z.number() }).nullable(),
  kind: z.enum([
    "production_queue",
    "map_plan",
    "flow_ast",
    "flow_commit",
    "group_composition",
    "params",
  ]),
  title_zh: z.string(),
  /** 空理由的提案不可接受（P3） */
  rationale_zh: z.string(),
  target: z.record(z.string(), z.unknown()),
  hunks: z.array(
    z.object({
      id: z.string(),
      kind: z.enum([
        "insert", "delete", "reorder", "modify",
        "add_mark", "move_mark", "rename_mark", "del_mark",
        "add_slot", "del_slot",
      ]),
      text_zh: z.string(),
      payload: z.record(z.string(), z.unknown()),
    }),
  ),
  validation: z
    .object({
      ok: z.boolean(),
      errors: z.array(
        z.object({ hunk_id: z.string().nullable(), text_zh: z.string() }),
      ),
    })
    .nullable(),
  preview: z
    .union([
      z.object({
        kind: z.literal("projection_pair"),
        current_ref: z.string(),
        proposed_ref: z.string(),
      }),
      z.object({
        kind: z.literal("map_overlay"),
        changed_slots: z.array(z.string()),
        changed_marks: z.array(z.string()),
      }),
      z.object({
        kind: z.literal("graph_diff"),
        added: z.array(z.string()),
        removed: z.array(z.string()),
        changed: z.array(z.string()),
      }),
    ])
    .nullable(),
  status: z.enum(["待审批", "已接受", "部分接受", "已拒绝", "已失效"]),
  decision: z
    .object({
      at: z.number(),
      accepted_hunks: z.array(z.string()),
      comment_zh: z.string().nullable(),
    })
    .nullable(),
});

export const zProposalsFrame = z.object({ proposals: z.array(zProposal) });

/* ---------------- 信封 ---------------- */

// topic 必须泛型到**具体字面量** K，否则 z.literal(topic) 会把每个成员的 topic 都推成整个
// Topic 联合，zAnyEnvelope 就失去判别能力（`if (env.topic === "frame/world")` 无法收窄 payload）。
const envelope = <K extends Topic, T extends z.ZodTypeAny>(topic: K, payload: T) =>
  z.object({
    topic: z.literal(topic),
    /** 不匹配时前端拒绝渲染并提示（红线 C8） */
    rev: z.literal(REV),
    seq: z.number().int(),
    /** 唯一时间基准（游戏秒）；所有节拍对齐它，不用墙钟 —— ADR-0025 §6 */
    game_time: z.number(),
    /** 墙钟毫秒，仅诊断，不参与语义 */
    wall_ms: z.number(),
    payload,
  });

export const PAYLOADS = {
  "static/map": zMapStatic,
  "static/catalog": zCatalogStatic,
  "static/schema": zSchemaStatic,
  "static/strategy": zStrategyStatic,
  "static/terrain": zTerrainFrame,
  "frame/session": zSessionFrame,
  "frame/world": zWorldFrame,
  "frame/flow": zFlowFrame,
  "frame/production": zProductionFrame,
  "frame/economy": zEconomyFrame,
  "frame/ops": zOpsFrame,
  "frame/projection": zProjectionFrame,
  "frame/alerts": zAlertsFrame,
  proposals: zProposalsFrame,
} as const;

export const ENVELOPES = {
  "static/map": envelope("static/map", zMapStatic),
  "static/catalog": envelope("static/catalog", zCatalogStatic),
  "static/schema": envelope("static/schema", zSchemaStatic),
  "static/strategy": envelope("static/strategy", zStrategyStatic),
  "static/terrain": envelope("static/terrain", zTerrainFrame),
  "frame/session": envelope("frame/session", zSessionFrame),
  "frame/world": envelope("frame/world", zWorldFrame),
  "frame/flow": envelope("frame/flow", zFlowFrame),
  "frame/production": envelope("frame/production", zProductionFrame),
  "frame/economy": envelope("frame/economy", zEconomyFrame),
  "frame/ops": envelope("frame/ops", zOpsFrame),
  "frame/projection": envelope("frame/projection", zProjectionFrame),
  "frame/alerts": envelope("frame/alerts", zAlertsFrame),
  proposals: envelope("proposals", zProposalsFrame),
} as const;

export const zAnyEnvelope = z.discriminatedUnion("topic", [
  ENVELOPES["static/map"],
  ENVELOPES["static/catalog"],
  ENVELOPES["static/schema"],
  ENVELOPES["static/strategy"],
  ENVELOPES["static/terrain"],
  ENVELOPES["frame/session"],
  ENVELOPES["frame/world"],
  ENVELOPES["frame/flow"],
  ENVELOPES["frame/production"],
  ENVELOPES["frame/economy"],
  ENVELOPES["frame/ops"],
  ENVELOPES["frame/projection"],
  ENVELOPES["frame/alerts"],
  ENVELOPES["proposals"],
]);

/* ---------------- 推导类型（不手写第二份） ---------------- */

export type GridB64 = z.infer<typeof zGridB64>;
export type Pt = z.infer<typeof zPt>;
export type Cell = z.infer<typeof zCell>;
export type MapStatic = z.infer<typeof zMapStatic>;
export type CatalogStatic = z.infer<typeof zCatalogStatic>;
export type SchemaStatic = z.infer<typeof zSchemaStatic>;
export type StrategyStatic = z.infer<typeof zStrategyStatic>;
export type TerrainFrame = z.infer<typeof zTerrainFrame>;
export type SessionFrame = z.infer<typeof zSessionFrame>;
export type WorldFrame = z.infer<typeof zWorldFrame>;
export type FlowFrame = z.infer<typeof zFlowFrame>;
export type ProductionFrame = z.infer<typeof zProductionFrame>;
export type EconomyFrame = z.infer<typeof zEconomyFrame>;
export type OpsFrame = z.infer<typeof zOpsFrame>;
export type ProjectionFrame = z.infer<typeof zProjectionFrame>;
export type AlertsFrame = z.infer<typeof zAlertsFrame>;
export type Proposal = z.infer<typeof zProposal>;
export type ProposalsFrame = z.infer<typeof zProposalsFrame>;

export type PayloadFor<K extends Topic> = z.infer<(typeof PAYLOADS)[K]>;
export type EnvelopeFor<K extends Topic> = z.infer<(typeof ENVELOPES)[K]>;
export type AnyEnvelope = z.infer<typeof zAnyEnvelope>;

/* ---------------- 校验入口 ---------------- */

export class ContractError extends Error {
  constructor(
    message: string,
    readonly detail?: unknown,
  ) {
    super(message);
    this.name = "ContractError";
  }
}

/** 解析一条信封；rev 不匹配或字段不合法一律抛错（不许静默降级） */
export function parseEnvelope(raw: unknown): AnyEnvelope {
  if (typeof raw !== "object" || raw === null) {
    throw new ContractError("信封不是对象");
  }
  const rev = (raw as { rev?: unknown }).rev;
  if (rev !== REV) {
    throw new ContractError(
      `契约版本不匹配：帧 rev=${String(rev)}，前端 REV=${REV}。请同步 docs/contract/plan-frontend.md §2`,
    );
  }
  const r = zAnyEnvelope.safeParse(raw);
  if (!r.success) {
    const topic = (raw as { topic?: unknown }).topic;
    throw new ContractError(
      `帧校验失败（topic=${String(topic)}）：${r.error.issues
        .slice(0, 3)
        .map((i) => `${i.path.join(".")} ${i.message}`)
        .join("; ")}`,
      r.error.issues,
    );
  }
  return r.data;
}

/** 解析一行 JSONL */
export function parseEnvelopeLine(line: string): AnyEnvelope {
  return parseEnvelope(JSON.parse(line) as unknown);
}