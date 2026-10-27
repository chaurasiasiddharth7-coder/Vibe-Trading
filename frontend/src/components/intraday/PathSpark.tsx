import { signedR, sparkGeometry, type IntradayTrade } from "@/lib/intraday";
import { cn } from "@/lib/utils";

const W = 112;
const H = 28;

/** One trade's R through the day: a plain SVG line with a dashed zero line. Static, never animated. */
export function PathSpark({ trade }: { trade: Pick<IntradayTrade, "path" | "r_gross" | "mfe_r" | "mae_r" | "symbol"> }) {
  const g = sparkGeometry(trade.path, W, H);
  if (!g) return <span className="w-28 shrink-0 text-xs text-muted-foreground" aria-hidden>—</span>;
  const up = trade.r_gross >= 0;
  return (
    <svg
      role="img"
      aria-label={`${trade.symbol} through the day: ended at ${signedR(trade.r_gross)}, best ${signedR(trade.mfe_r)}, worst ${signedR(trade.mae_r)} before costs`}
      width={W}
      height={H}
      viewBox={`0 0 ${W} ${H}`}
      className="shrink-0"
    >
      <line x1={0} x2={W} y1={g.zeroY} y2={g.zeroY} className="stroke-border" strokeDasharray="2 3" />
      <polyline points={g.points} fill="none" strokeWidth={1.5} className={cn(up ? "stroke-emerald-400" : "stroke-rose-400")} />
      <circle cx={g.endX} cy={g.endY} r={2.2} className={cn(up ? "fill-emerald-400" : "fill-rose-400")} />
    </svg>
  );
}
