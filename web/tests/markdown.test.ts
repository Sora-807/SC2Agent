/** 迷你 Markdown 块级解析（ChatDock agent 回复）—— 白名单行为锁死 */
import { describe, expect, it } from "vitest";
import { markdownToBlocks } from "../src/shell/markdown";

describe("markdownToBlocks", () => {
  it("标题/段落/列表/代码块/引用/分割线各归各类", () => {
    const blocks = markdownToBlocks([
      "## 战术结论",
      "",
      "先**补气**再下工厂：",
      "- 精炼厂 → 蓝方主矿气井1",
      "- 派 3 人采气",
      "",
      "1. 第一步",
      "2. 第二步",
      "",
      "```yaml",
      "queue: []",
      "```",
      "> 用户偏好：别提前插补给",
      "",
      "---",
    ].join("\n"));
    expect(blocks).toEqual([
      { kind: "h", level: 2, text: "战术结论" },
      { kind: "p", text: "先**补气**再下工厂：" },
      { kind: "ul", items: ["精炼厂 → 蓝方主矿气井1", "派 3 人采气"] },
      { kind: "ol", items: ["第一步", "第二步"] },
      { kind: "code", lang: "yaml", text: "queue: []" },
      { kind: "quote", text: "用户偏好：别提前插补给" },
      { kind: "hr" },
    ]);
  });

  it("未闭合代码块容错（吃到末尾，不抛不丢）", () => {
    const blocks = markdownToBlocks("```python\nprint(1)");
    expect(blocks).toEqual([{ kind: "code", lang: "python", text: "print(1)" }]);
  });

  it("中文序号列表（1、）也认", () => {
    expect(markdownToBlocks("1、开局\n2、过渡")).toEqual([
      { kind: "ol", items: ["开局", "过渡"] },
    ]);
  });

  it("纯文本一段、空行分段", () => {
    expect(markdownToBlocks("第一段\n延续\n\n第二段")).toEqual([
      { kind: "p", text: "第一段\n延续" },
      { kind: "p", text: "第二段" },
    ]);
  });

  it("GFM 表格：表头+分隔行+数据行，对齐变体都认", () => {
    const blocks = markdownToBlocks([
      "| 项目 | 数值 |",
      "|:---|---:|",
      "| 矿 | 1222 |",
      "| 气 | 287 |",
    ].join("\n"));
    expect(blocks).toEqual([{
      kind: "table",
      head: ["项目", "数值"],
      rows: [["矿", "1222"], ["气", "287"]],
    }]);
  });

  it("表格容错：缺分隔行也收（首行当表头）", () => {
    const blocks = markdownToBlocks("| a | b |\n| 1 | 2 |");
    expect(blocks).toEqual([{ kind: "table", head: ["a", "b"], rows: [["1", "2"]] }]);
  });

  it("表格容错：列数不齐补空、缺首尾竖线能救", () => {
    const blocks = markdownToBlocks("项 | 值\n--- | ---\n矿 | 1222\n气");  // 最后混进非表格行
    expect(blocks[0]).toEqual({
      kind: "table", head: ["项", "值"], rows: [["矿", "1222"]],
    });
    expect(blocks[1]).toEqual({ kind: "p", text: "气" });
  });

  it("单个竖线的普通文本不是表格", () => {
    expect(markdownToBlocks("矿 | 气是两种资源")).toEqual([
      { kind: "p", text: "矿 | 气是两种资源" },
    ]);
  });
});
