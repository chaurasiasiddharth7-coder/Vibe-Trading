"""BTST (buy today, sell tomorrow) on the day's top NSE gainers.

At about 15:20 IST the scan ranks the market's top gainers, puts plain-language warnings on each
one, and picks the strongest gainer with no warning. The plan is a GTT OCO in the user's own broker
app: sell at +1% (take-profit) or at -2% (safety stop); if neither fills, sell near the next close.
Every live day is filed with the pick *and* all candidates, then settled from the next day's prices,
so the track record and the warnings' own record grow from real days. Replayed history from the
NSE store is filed separately and never blended with live days.

Read-only: nothing here places an order.
"""
