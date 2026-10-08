import type { LiveStatus } from "../lib/useEvents";

export function LiveDot({ status }: { status: LiveStatus }) {
  const cls =
    status === "live"
      ? "bg-emerald-500 shadow-[0_0_0_3px_rgba(16,185,129,0.25)]"
      : status === "connecting"
        ? "bg-zinc-400 animate-pulse"
        : "bg-amber-500 shadow-[0_0_0_3px_rgba(245,158,11,0.3)]";
  const label = status === "live" ? "live" : status === "connecting" ? "connecting" : "reconnecting";
  return (
    <span className="inline-flex items-center gap-1.5 text-[11px] text-mute" title={`SSE /api/events: ${status}`}>
      <span className={`inline-block h-2 w-2 rounded-full ${cls}`} />
      {label}
    </span>
  );
}
