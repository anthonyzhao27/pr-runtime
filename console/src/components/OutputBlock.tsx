import { useState } from "react";

interface Props {
  title: string;
  output: string | null | undefined;
  rc?: number | null;
  collapseLines?: number;
}

export function OutputBlock({ title, output, rc, collapseLines = 40 }: Props) {
  const [open, setOpen] = useState(false);
  const text = output ?? "";
  const lines = text.split("\n");
  const long = lines.length > collapseLines;
  const shown = long && !open ? lines.slice(-collapseLines).join("\n") : text;

  const rcCls =
    rc === null || rc === undefined
      ? "text-mute"
      : rc === 0
        ? "text-emerald-600 dark:text-emerald-400"
        : "text-red-600 dark:text-red-400";

  return (
    <div className="rounded border border-line overflow-hidden">
      <div className="flex items-center gap-3 px-3 h-[30px] bg-panel border-b border-line text-[12px]">
        <span className="font-medium">{title}</span>
        {rc !== undefined && (
          <span className={`font-mono text-[11px] ${rcCls}`}>rc={rc === null ? "—" : rc}</span>
        )}
        <span className="font-mono text-[11px] text-mute">{lines.length} lines</span>
        {long && (
          <button
            type="button"
            onClick={() => setOpen((o) => !o)}
            className="ml-auto text-[11px] text-accent hover:underline"
          >
            {open ? "collapse" : `show all (last ${collapseLines} shown)`}
          </button>
        )}
      </div>
      {text ? (
        <pre className="px-3 py-2 font-mono text-[12px] leading-[18px] overflow-x-auto whitespace-pre max-h-[520px] overflow-y-auto">
          {shown}
        </pre>
      ) : (
        <div className="px-3 py-3 text-[12px] text-mute">no output</div>
      )}
    </div>
  );
}
