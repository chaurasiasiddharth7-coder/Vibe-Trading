import { cn } from "@/lib/utils";
import type { ScoreTone } from "@/components/desk/score";

const TONE: Record<ScoreTone, { text: string; bar: string }> = {
  strong: { text: "text-emerald-400", bar: "bg-emerald-500" },
  good: { text: "text-emerald-300", bar: "bg-emerald-500/80" },
  okay: { text: "text-amber-300", bar: "bg-amber-500" },
  weak: { text: "text-orange-300", bar: "bg-orange-500" },
  avoid: { text: "text-rose-400", bar: "bg-rose-500" },
};

/** A 0 to 10 score with a segmented meter and a one-word verdict (never colour alone). */
export function ScoreBadge({
  score,
  tone,
  verdict,
  label = "Trade score",
  compact = false,
}: {
  score: number;
  tone: ScoreTone;
  verdict: string;
  label?: string;
  compact?: boolean;
}) {
  const filled = Math.round(score);
  return (
    <div className={cn("flex items-center gap-3", compact && "gap-2")}>
      <div className="leading-none" aria-hidden={false}>
        <span className={cn("font-mono font-semibold", TONE[tone].text, compact ? "text-lg" : "text-3xl")}>{score.toFixed(1)}</span>
        <span className={cn("font-mono text-muted-foreground", compact ? "text-xs" : "text-sm")}>/10</span>
      </div>
      <div className="min-w-0 space-y-1">
        <div
          role="meter"
          aria-label={label}
          aria-valuemin={0}
          aria-valuemax={10}
          aria-valuenow={score}
          aria-valuetext={`${score.toFixed(1)} out of 10, ${verdict}`}
          className="flex gap-0.5"
        >
          {Array.from({ length: 10 }, (_, i) => (
            <span key={i} className={cn("h-1.5 rounded-sm", compact ? "w-1.5" : "w-3", i < filled ? TONE[tone].bar : "bg-muted")} />
          ))}
        </div>
        <p className={cn("font-semibold", TONE[tone].text, compact ? "text-xs" : "text-sm")}>{verdict}</p>
      </div>
    </div>
  );
}
