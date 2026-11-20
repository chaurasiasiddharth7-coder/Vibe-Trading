import { useEffect, useRef, useState } from "react";
import { watchlistApi, type QuotesResponse } from "@/lib/watchlistApi";

/** Poll quotes: every 3 s while Kite is live, every minute on end-of-day prices; paused when the tab is hidden. */
export function useQuotes(symbols: string[], indices = true) {
  const [data, setData] = useState<QuotesResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const key = symbols.join(",");
  const liveRef = useRef(false);

  useEffect(() => {
    let alive = true;
    let timer: number | undefined;
    const list = key ? key.split(",") : [];
    const tick = async () => {
      if (document.visibilityState === "visible") {
        try {
          const res = await watchlistApi.quotes(list, indices);
          if (!alive) return;
          liveRef.current = res.live;
          setData(res);
          setError(null);
        } catch (e) {
          if (alive) setError(e instanceof Error ? e.message : "Couldn't load prices");
        }
      }
      if (alive) timer = window.setTimeout(tick, liveRef.current ? 3000 : 60000);
    };
    const onShow = () => {
      if (document.visibilityState === "visible") {
        window.clearTimeout(timer);
        void tick();
      }
    };
    void tick();
    document.addEventListener("visibilitychange", onShow);
    return () => {
      alive = false;
      window.clearTimeout(timer);
      document.removeEventListener("visibilitychange", onShow);
    };
  }, [key, indices]);

  return { data, error };
}
