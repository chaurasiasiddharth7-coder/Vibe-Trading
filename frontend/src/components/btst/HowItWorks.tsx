/** The rules in plain words, and what the research found. Closed by default. */
export function HowItWorks() {
  return (
    <details className="text-sm">
      <summary className="cursor-pointer min-h-11 flex items-center font-semibold">How the rules work, and how far to trust them</summary>
      <div className="space-y-3 pb-3 max-w-3xl text-muted-foreground">
        <ol className="list-decimal pl-5 space-y-1">
          <li>At about 15:20 the scan ranks NSE stocks by today's gain. It keeps EQ stocks up at least 2%, priced ₹50 or more, trading ₹10 crore or more today and usually at least ₹2 crore a day. It drops stocks stuck at the upper price band, because there is no one to buy from.</li>
          <li>The top 20 are checked against seven warnings: volume blow-off (over 10x usual), up over 12% today, already up over 9% in 5 days or 19% in 20, a weak market (under 36% of stocks up), a downtrend, a gap-up over 2% at the open, and a stock that normally swings over 4% a day.</li>
          <li>The pick is the highest gainer with no warning. If every candidate has one, there is no trade that day.</li>
          <li>Buy for delivery (CNC). Place one GTT OCO for the next day: sell at +1% (take-profit) or at −2% (safety stop). If neither fills by 15:20 the next day, sell at market.</li>
        </ol>
        <p><span className="text-foreground font-semibold">What the testing found.</span> On NSE prices from 2021 to November 2026, buying the plain #1 gainer and selling at +1% lost about 0.9% a trade after costs: it hit +1% 73% of the time, but the misses averaged −5.4%. The warnings were set on 2021–22 only. On 2023–26, which they had not seen, the top gainer with no warning made about +0.2% a trade after costs and hit +1% about 78% of the time. 2025 lost money.</p>
        <p><span className="text-foreground font-semibold">Stop-loss and trailing stop.</span> Checked on 1-minute prices (Aug 2023 – Feb 2024, 99 stocks), a 2% stop cost about 0.05–0.15% a trade on average, and in the replay it removed almost every 5%+ loss: only gap-downs at the open get through. A tighter 1% stop did not help. Trailing the stop after +1% added about 0.1–0.3% a trade on the minute data, far less than daily-bar tests suggest, so it is not part of these rules.</p>
        <p><span className="text-foreground font-semibold">Limits.</span> The edge is small: an extra 0.2% of slippage a trade wipes it out, so use limit orders and liquid names. Past days are rebuilt from closing prices standing in for 15:20. Live days settle on 5-minute prices when they are available. This is a practice tool; it is not advice from a registered adviser, and nothing here places an order.</p>
      </div>
    </details>
  );
}
