"""Indian intraday pack: scanner, entry timer, exit planner, trade replay, calibration.

An NSE adaptation of the opening-range-breakout method in Jakeschincariol/day-trader-skill
(trader-scanner / trader-entry / trader-exit / trader-timemachine, MIT), rebuilt for the NSE
session (09:15-15:30 IST, 75 five-minute bars), 0.05 ticks, same-day shorts and real intraday
costs (STT, exchange, SEBI, stamp, GST, brokerage, slippage). Pure rules live in ``rules`` and
``costs``; ``bars`` fetches and stores 5-minute data; ``scanner``, ``timemachine`` and
``calibrate`` build on them. Education and tooling, not advice: nothing here places an order.
"""
