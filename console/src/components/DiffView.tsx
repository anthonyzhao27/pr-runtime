import { useMemo } from "react";

type Kind = "file" | "hunk" | "add" | "del" | "ctx" | "meta";

interface Line {
  kind: Kind;
  text: string;
  oldNo: number | null;
  newNo: number | null;
}

function parse(diff: string): Line[] {
  const out: Line[] = [];
  let oldNo = 0;
  let newNo = 0;
  for (const raw of diff.split("\n")) {
    if (raw.startsWith("diff --git ")) {
      out.push({ kind: "file", text: raw, oldNo: null, newNo: null });
    } else if (raw.startsWith("@@")) {
      const m = /^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@/.exec(raw);
      oldNo = m ? parseInt(m[1] ?? "0", 10) : 0;
      newNo = m ? parseInt(m[2] ?? "0", 10) : 0;
      out.push({ kind: "hunk", text: raw, oldNo: null, newNo: null });
    } else if (raw.startsWith("+++") || raw.startsWith("---") || raw.startsWith("index ") || raw.startsWith("new file") || raw.startsWith("deleted file") || raw.startsWith("similarity") || raw.startsWith("rename ") || raw.startsWith("old mode") || raw.startsWith("new mode") || raw.startsWith("Binary files")) {
      out.push({ kind: "meta", text: raw, oldNo: null, newNo: null });
    } else if (raw.startsWith("+")) {
      out.push({ kind: "add", text: raw.slice(1), oldNo: null, newNo: newNo++ });
    } else if (raw.startsWith("-")) {
      out.push({ kind: "del", text: raw.slice(1), oldNo: oldNo++, newNo: null });
    } else if (raw.startsWith("\\")) {
      out.push({ kind: "meta", text: raw, oldNo: null, newNo: null });
    } else {
      // context (leading space) or blank trailing line
      out.push({ kind: "ctx", text: raw.startsWith(" ") ? raw.slice(1) : raw, oldNo: oldNo++, newNo: newNo++ });
    }
  }
  // Drop a trailing empty context line produced by the final newline.
  const last = out[out.length - 1];
  if (last && last.kind === "ctx" && last.text === "") out.pop();
  return out;
}

const ROW: Record<Kind, string> = {
  file: "bg-panel text-fg font-semibold border-t border-line",
  hunk: "bg-accent/5 text-accent/90",
  meta: "text-mute",
  add: "bg-emerald-500/10 text-emerald-900 dark:text-emerald-200",
  del: "bg-red-500/10 text-red-900 dark:text-red-200",
  ctx: "",
};

const GUTTER: Record<Kind, string> = {
  file: "",
  hunk: "",
  meta: "",
  add: "bg-emerald-500/15",
  del: "bg-red-500/15",
  ctx: "",
};

export function DiffView({ diff }: { diff: string }) {
  const lines = useMemo(() => parse(diff), [diff]);
  const stats = useMemo(() => {
    let add = 0;
    let del = 0;
    for (const l of lines) {
      if (l.kind === "add") add++;
      else if (l.kind === "del") del++;
    }
    return { add, del };
  }, [lines]);

  return (
    <div className="rounded border border-line overflow-hidden">
      <div className="flex items-center gap-3 px-3 h-[30px] bg-panel border-b border-line text-[11px] text-mute">
        <span className="font-mono">{lines.filter((l) => l.kind === "file").length} files</span>
        <span className="font-mono text-emerald-600 dark:text-emerald-400">+{stats.add}</span>
        <span className="font-mono text-red-600 dark:text-red-400">−{stats.del}</span>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full border-collapse font-mono text-[12px] leading-[20px]">
          <tbody>
            {lines.map((l, i) => {
              const marker = l.kind === "add" ? "+" : l.kind === "del" ? "−" : l.kind === "ctx" ? " " : "";
              if (l.kind === "file" || l.kind === "hunk" || l.kind === "meta") {
                return (
                  <tr key={i} className={ROW[l.kind]}>
                    <td colSpan={3} className={`px-3 whitespace-pre ${l.kind === "file" ? "py-1" : ""}`}>
                      {l.text}
                    </td>
                  </tr>
                );
              }
              return (
                <tr key={i} className={ROW[l.kind]}>
                  <td className={`select-none text-right pr-2 pl-3 w-[44px] text-mute/70 tabular-nums ${GUTTER[l.kind]}`}>
                    {l.oldNo ?? ""}
                  </td>
                  <td className={`select-none text-right pr-2 w-[44px] text-mute/70 tabular-nums ${GUTTER[l.kind]}`}>
                    {l.newNo ?? ""}
                  </td>
                  <td className="pl-2 pr-3 whitespace-pre">
                    <span className="inline-block w-[12px] select-none opacity-70">{marker}</span>
                    {l.text}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
