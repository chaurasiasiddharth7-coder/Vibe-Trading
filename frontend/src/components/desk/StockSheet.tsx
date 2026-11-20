import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { ExternalLink, X } from "lucide-react";
import { cn } from "@/lib/utils";
import { SymbolInsight } from "@/components/desk/SymbolInsight";
import { useQuotes } from "@/hooks/useQuotes";
import { fmtChange, fmtPrice, tone } from "@/lib/watchlistApi";

interface SheetApi {
  open: (symbol: string) => void;
}

const Ctx = createContext<SheetApi | null>(null);

const clean = (s: string) => s.toUpperCase().replace(/\.NS$|\.BO$/, "").replace(/[^A-Z0-9&-]/g, "");

/** Tap any stock name to see its chart in a pop-up. Wrap the app once; use <StockLink> or useStockSheet(). */
export function StockSheetProvider({ children }: { children: ReactNode }) {
  const [symbol, setSymbol] = useState<string | null>(null);
  const open = useCallback((s: string) => {
    const c = clean(s);
    if (c) setSymbol(c);
  }, []);
  const value = useMemo(() => ({ open }), [open]);
  return (
    <Ctx.Provider value={value}>
      {children}
      {symbol && <StockSheet symbol={symbol} onClose={() => setSymbol(null)} />}
    </Ctx.Provider>
  );
}

/** Null outside the provider (pages also render in the local app without it). */
export function useStockSheet(): SheetApi | null {
  return useContext(Ctx);
}

/** A stock name that opens its chart. Falls back to plain text where the pop-up isn't available. */
export function StockLink({ symbol, children, className }: { symbol: string; children?: ReactNode; className?: string }) {
  const sheet = useStockSheet();
  if (!sheet) return <span className={className}>{children ?? symbol}</span>;
  return (
    <button
      type="button"
      onClick={(e) => {
        e.stopPropagation();
        e.preventDefault();
        sheet.open(symbol);
      }}
      className={cn("underline decoration-dotted decoration-muted-foreground/60 underline-offset-4 hover:text-primary hover:decoration-primary text-start", className)}
      title={`Open ${clean(symbol)} chart`}
    >
      {children ?? symbol}
    </button>
  );
}

function StockSheet({ symbol, onClose }: { symbol: string; onClose: () => void }) {
  const { data } = useQuotes([symbol], false);
  const q = data?.quotes[symbol];
  const closeRef = useRef<HTMLButtonElement | null>(null);
  const [wide, setWide] = useState(() => window.matchMedia?.("(min-width: 768px)").matches ?? true);

  useEffect(() => {
    const prev = document.activeElement as HTMLElement | null;
    closeRef.current?.focus();
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    const mq = window.matchMedia?.("(min-width: 768px)");
    const onMq = () => setWide(!!mq?.matches);
    document.addEventListener("keydown", onKey);
    mq?.addEventListener?.("change", onMq);
    const overflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKey);
      mq?.removeEventListener?.("change", onMq);
      document.body.style.overflow = overflow;
      prev?.focus?.();
    };
  }, [onClose]);

  return (
    <div className="fixed inset-0 z-50 flex items-end md:items-center justify-center" role="presentation">
      <button type="button" aria-label="Close chart" tabIndex={-1} onClick={onClose} className="absolute inset-0 bg-black/50 cursor-default" />
      <div
        role="dialog"
        aria-modal="true"
        aria-label={`${symbol} chart`}
        className="relative w-full md:max-w-4xl max-h-[92vh] md:max-h-[88vh] overflow-y-auto bg-card text-card-foreground rounded-t-2xl md:rounded-xl shadow-2xl pb-[env(safe-area-inset-bottom)]"
      >
        <div className="sticky top-0 z-10 bg-card/95 backdrop-blur border-b border-border px-4 pt-2 pb-3">
          <div className="mx-auto mb-2 h-1 w-10 rounded-full bg-muted-foreground/30 md:hidden" aria-hidden />
          <div className="flex items-start gap-3">
            <div className="min-w-0 flex-1">
              <h2 className="text-lg font-semibold">
                {symbol} <span className="text-xs font-normal text-muted-foreground">NSE</span>
              </h2>
              <p className="tabular-nums">
                <span className={cn("text-xl font-semibold", tone(q?.change))}>{fmtPrice(q?.ltp)}</span>{" "}
                <span className="text-sm text-muted-foreground">{q ? fmtChange(q) : ""}</span>
                {data && (
                  <span className="ms-2 text-[11px] text-muted-foreground">{data.live ? "Live from Kite" : `End of day${q?.as_of ? ` · ${q.as_of}` : ""}`}</span>
                )}
              </p>
            </div>
            <Link
              to={`/dashboard?tab=chart&symbol=${encodeURIComponent(symbol)}`}
              onClick={onClose}
              className="min-h-10 px-3 inline-flex items-center gap-1 rounded-md text-sm text-primary hover:bg-muted"
            >
              Full page <ExternalLink className="h-3.5 w-3.5" aria-hidden />
            </Link>
            <button ref={closeRef} type="button" onClick={onClose} aria-label="Close" className="h-10 w-10 grid place-items-center rounded-full hover:bg-muted">
              <X className="h-5 w-5" aria-hidden />
            </button>
          </div>
        </div>
        <div className="p-4">
          <SymbolInsight symbol={symbol} period="1y" compact={!wide} chartHeight={wide ? 460 : 380} refreshMs={data?.live ? 30000 : undefined} />
        </div>
      </div>
    </div>
  );
}
