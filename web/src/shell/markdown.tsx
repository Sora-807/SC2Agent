/**
 * 迷你 Markdown 渲染（ChatDock agent 回复用，2026-08-22 用户要求）——
 * 零依赖、显式白名单：标题/粗斜体/行内码/围栏代码/有序无序列表/引用/链接/
 * GFM 表格（带容错：缺分隔行、列数不齐、缺首尾竖线都救得回来）。
 * **刻意不支持** HTML（agent 输出直接进 React 元素，不存在注入面）。
 * 纯解析在 markdownToBlocks（可测），<Markdown> 只是薄渲染层。
 */
import type { ReactNode } from "react";

type Block =
  | { kind: "h"; level: number; text: string }
  | { kind: "p"; text: string }
  | { kind: "code"; lang: string; text: string }
  | { kind: "ul"; items: string[] }
  | { kind: "ol"; items: string[] }
  | { kind: "quote"; text: string }
  | { kind: "table"; head: string[]; rows: string[][] }
  | { kind: "hr" };

/** 表格行解析（容错）：剥首尾竖线、按 | 切、trim；<2 格或全空不算表格行 */
function pipeRow(line: string): string[] | null {
  const t = line.trim();
  if (!t.includes("|")) return null;
  // 不要求首尾竖线（LLM 常漏）；防误伤靠外层「连续 ≥2 行竖线行」条件
  const cells = t.replace(/^\|/, "").replace(/\|$/, "").split("|").map((c) => c.trim());
  return cells.length >= 2 && cells.some((c) => c !== "") ? cells : null;
}

/** 分隔行：|---|:---:|---:| 各对齐变体（全格皆短横线） */
function isSeparatorRow(cells: string[]): boolean {
  return cells.length >= 2 && cells.every((c) => /^:?-{1,}:?$/.test(c));
}

/** 块级解析（导出给测试；行内格式由 renderInline 在渲染时处理） */
export function markdownToBlocks(text: string): Block[] {
  const lines = text.replace(/\r\n/g, "\n").split("\n");
  const out: Block[] = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i]!;
    if (line.startsWith("```")) {
      const lang = line.slice(3).trim();
      const buf: string[] = [];
      i += 1;
      while (i < lines.length && !lines[i]!.startsWith("```")) {
        buf.push(lines[i]!);
        i += 1;
      }
      i += 1; // 收尾 ```（没有就吃到末尾，容错）
      out.push({ kind: "code", lang, text: buf.join("\n") });
      continue;
    }
    const h = /^(#{1,4})\s+(.*)$/.exec(line);
    if (h) {
      out.push({ kind: "h", level: h[1]!.length, text: h[2]! });
      i += 1;
      continue;
    }
    if (/^\s*(-{3,}|\*{3,})\s*$/.test(line)) {
      out.push({ kind: "hr" });
      i += 1;
      continue;
    }
    if (/^\s*[-*]\s+/.test(line)) {
      const items: string[] = [];
      while (i < lines.length && /^\s*[-*]\s+/.test(lines[i]!)) {
        items.push(lines[i]!.replace(/^\s*[-*]\s+/, ""));
        i += 1;
      }
      out.push({ kind: "ul", items });
      continue;
    }
    if (/^\s*\d+[.、]\s*/.test(line)) {
      const items: string[] = [];
      while (i < lines.length && /^\s*\d+[.、]\s*/.test(lines[i]!)) {
        items.push(lines[i]!.replace(/^\s*\d+[.、]\s*/, ""));
        i += 1;
      }
      out.push({ kind: "ol", items });
      continue;
    }
    // GFM 表格：表头 + 分隔行 + 数据行；容错 = 连续竖线行但没有分隔行也收
    const head = pipeRow(line);
    if (head != null && i + 1 < lines.length) {
      const next = pipeRow(lines[i + 1]!);
      const proper = next != null && isSeparatorRow(next);
      if (proper || next != null) {
        const rows: string[][] = [];
        i += proper ? 2 : 1;            // 没分隔行：首行仍当表头，不重复进数据行
        while (i < lines.length) {
          const cells = pipeRow(lines[i]!);
          if (cells == null) break;
          rows.push(cells);
          i += 1;
        }
        // 列数取最宽行，短行补空（LLM 常漏格）
        const width = Math.max(head.length, ...(rows.length ? rows.map((r) => r.length) : [0]));
        const pad = (r: string[]): string[] => {
          const out2 = [...r];
          while (out2.length < width) out2.push("");
          return out2.slice(0, width);
        };
        out.push({ kind: "table", head: pad(head), rows: rows.map(pad) });
        continue;
      }
    }
    if (line.startsWith("> ")) {
      const buf: string[] = [];
      while (i < lines.length && lines[i]!.startsWith("> ")) {
        buf.push(lines[i]!.slice(2));
        i += 1;
      }
      out.push({ kind: "quote", text: buf.join(" ") });
      continue;
    }
    if (line.trim() === "") {
      i += 1;
      continue;
    }
    // 段落：吃到下一个空行/块级开头
    const buf = [line];
    i += 1;
    while (i < lines.length && lines[i]!.trim() !== "" && !/^(#{1,4}\s|```|\s*[-*]\s|\s*\d+[.、]\s|> )/.test(lines[i]!)) {
      buf.push(lines[i]!);
      i += 1;
    }
    out.push({ kind: "p", text: buf.join("\n") });
  }
  return out;
}

/** 行内：**粗** / *斜* / `码` / [字](链) —— 用占位切分避免嵌套正则的回溯灾难 */
function renderInline(text: string): ReactNode[] {
  const parts: ReactNode[] = [];
  let rest = text;
  let key = 0;
  const re = /(\*\*([^*]+)\*\*)|(`([^`]+)`)|(\[([^\]]+)\]\(([^)]+)\))|(\*([^*]+)\*)/;
  while (rest.length > 0) {
    const m = re.exec(rest);
    if (!m) {
      parts.push(rest);
      break;
    }
    if (m.index > 0) parts.push(rest.slice(0, m.index));
    if (m[2] != null) {
      parts.push(<strong key={key++}>{m[2]}</strong>);
    } else if (m[4] != null) {
      parts.push(<code key={key++} className="rounded bg-inset px-1 font-mono text-[0.92em] text-dim">{m[4]}</code>);
    } else if (m[6] != null) {
      parts.push(<a key={key++} href={m[7]} target="_blank" rel="noreferrer" className="text-blue-fg underline">{m[6]}</a>);
    } else if (m[8] != null) {
      parts.push(<em key={key++}>{m[8]}</em>);
    }
    rest = rest.slice(m.index + m[0].length);
  }
  return parts;
}

export function Markdown(props: { text: string }): ReactNode {
  const blocks = markdownToBlocks(props.text);
  return (
    <div className="space-y-1.5">
      {blocks.map((b, i) => {
        switch (b.kind) {
          case "h":
            return <div key={i} className={b.level <= 2 ? "font-semibold text-strong" : "font-medium text-dim"}>{renderInline(b.text)}</div>;
          case "code":
            return <pre key={i} className="overflow-auto rounded-md border border-l1 bg-inset p-2 font-mono text-mono text-dim">{b.text}</pre>;
          case "ul":
            return <ul key={i} className="list-disc space-y-0.5 pl-5">{b.items.map((it, j) => <li key={j}>{renderInline(it)}</li>)}</ul>;
          case "ol":
            return <ol key={i} className="list-decimal space-y-0.5 pl-5">{b.items.map((it, j) => <li key={j}>{renderInline(it)}</li>)}</ol>;
          case "quote":
            return <blockquote key={i} className="border-l-2 border-l2 pl-2 text-faint">{renderInline(b.text)}</blockquote>;
          case "table":
            return (
              <div key={i} className="overflow-x-auto">
                <table className="w-full border-collapse text-note">
                  <thead>
                    <tr>{b.head.map((c, j) => (
                      <th key={j} className="border-b border-l2 px-1.5 py-1 text-left font-medium text-faint">{renderInline(c)}</th>
                    ))}</tr>
                  </thead>
                  <tbody>
                    {b.rows.map((r, ri) => (
                      <tr key={ri}>{r.map((c, ci) => (
                        <td key={ci} className="border-b border-l1 px-1.5 py-1 text-dim">{renderInline(c)}</td>
                      ))}</tr>
                    ))}
                  </tbody>
                </table>
              </div>
            );
          case "hr":
            return <hr key={i} className="border-l1" />;
          default:
            return <p key={i} className="whitespace-pre-wrap">{renderInline(b.text)}</p>;
        }
      })}
    </div>
  );
}
