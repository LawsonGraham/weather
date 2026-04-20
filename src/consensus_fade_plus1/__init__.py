"""Consensus-Fade +1 Offset — live Polymarket weather strategy (Nautilus).

Package layout (4 files, read in this order):

    discover.py    — what to trade today (consensus-tight +1 buckets)
    strategy.py    — the Nautilus Strategy (placing resting NO-buys)
    node.py        — TradingNode builder + live runner
    cli.py         — operator entry point

Plus:
    setup.py       — one-time wallet bootstrap
    backtest.py    — historical reproducer
    STRATEGY.md    — design doc (thesis, signal, backtest stats)
    ARCHITECTURE.md — how these pieces fit together
"""
