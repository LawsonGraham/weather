"""Consensus-Fade +1 Offset — continuous polling strategy with live rollover.

Mental model: a `while True` loop that polls our data functions every
~0.5s. Each tick:

  1. Refresh state from data sources:
       - subscribed set: markets we're receiving book deltas for. Grows
         as discover finds new qualifying markets (auto-loaded + subscribed
         transparently via Nautilus). Shrinks when a market resolves.
       - active set: subset of subscribed whose market_date equals
         THIS AIRPORT'S current local date. Rebuilt every tick from
         `discovered_markets` so each airport flips at its own local
         midnight, not at a shared UTC boundary. This is the set the
         take logic fires on (after also passing local-hour gate and
         market-wisdom cap).
       - positions: per-market shares owned, updated locally on fills.
  2. For each active market: if room under cap AND asks in range, fire
     an IOC BUY sized to sweep them.
  3. Sleep tick_interval, repeat.

Continuous operation across UTC midnight:
  - The daemon refreshes markets.parquet every ~1h, picking up new days'
    markets ~4h after Polymarket lists them.
  - Every ~60s the tick re-runs discover for today AND the next
    `lookahead_days`. Any new instrument seen → subscribe_order_book_deltas
    (Nautilus's auto-load fetches the instrument from Gamma and wires up
    the WSS sub, with the instrument landing in the cache before any
    messages flow).
  - Every ~5min the tick checks `instrument.expiration_ns` on the
    subscribed set. Anything past expiry → unsubscribe.

No resting limit orders. Every buy is IOC — it crosses against existing
asks at <= max_no_price, takes what it can, cancels any unfilled
remainder. If nothing in-range is on the book right now, we wait for
it to show up and try again on the next tick.

Data flow:
  features.parquet + markets.parquet  -->  discover_tradeable_markets
                                              -->  subscribed / active
  Polymarket WSS                      -->  cache.order_book(iid)
                                              -->  takeable qty
  fills                               -->  self._state.positions
                                              -->  per-market cap
"""
from __future__ import annotations

import asyncio
import traceback
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from nautilus_trader.common.component import TimeEvent
from nautilus_trader.common.enums import LogColor
from nautilus_trader.config import StrategyConfig
from nautilus_trader.model.enums import BookType, OrderSide, TimeInForce
from nautilus_trader.model.events import (
    OrderAccepted,
    OrderCanceled,
    OrderFilled,
    OrderRejected,
    OrderSubmitted,
)
from nautilus_trader.model.identifiers import ClientOrderId, InstrumentId
from nautilus_trader.model.objects import Price, Quantity
from nautilus_trader.trading.strategy import Strategy

from consensus_fade_plus1.persistence import BookSnapshotWriter, LedgerWriter
from lib.weather.timezones import CITY_TO_TZ

REPO_ROOT = Path(__file__).resolve().parents[2]
FEATURES_PATH = REPO_ROOT / "data" / "processed" / "backtest_v3" / "features.parquet"
MARKETS_PATH = REPO_ROOT / "data" / "processed" / "polymarket_weather" / "markets.parquet"

BOOK_SNAPSHOT_INTERVAL = timedelta(minutes=10)
BOOK_DEPTH = 10  # levels subscribed + persisted per side

# How often the tick runs the subscribe-sweep + expired-unsubscribe scans.
# Discovery is cheap (mtime-gated) so we can afford frequent checks, but we
# don't need sub-second cadence for something that changes daily.
EXPIRY_CHECK_INTERVAL_NS = 5 * 60 * 1_000_000_000  # 5 min

# Don't re-submit an IOC on the same instrument for this long after a reject.
# Prevents a tight TAKE→REJECT loop from spamming the exchange (common if
# the account is geoblocked, signature-invalid, or the venue is degraded).
REJECT_COOLDOWN_NS = 5 * 60 * 1_000_000_000  # 5 min

# If we accumulate this many consecutive rejects without any fill,
# flip the circuit breaker and stop submitting orders. Manual restart
# (cfp run again) resets it.
CIRCUIT_BREAKER_THRESHOLD = 20


# -----------------------------------------------------------------------------
# Config
# -----------------------------------------------------------------------------

class ConsensusFadeConfig(StrategyConfig, frozen=True):
    """Parameters for the continuous-polling strategy."""

    # Initial Nautilus InstrumentId strings to subscribe at on_start.
    # The tick loop handles rollover from here — new markets get subscribed
    # automatically as they appear; resolved markets get unsubscribed.
    instrument_ids: list[str]

    # Price ceiling for our IOC BUYs. The v2+cap rule pays NO up to ~0.99
    # per share because the `max_yes_ask` filter already restricts trades
    # to high-NO-price regimes where the market agrees the +1 won't
    # resolve. A tight ceiling here would reject otherwise-valid trades.
    max_no_price: float = 0.99

    # Slippage cap: only lift asks within this many dollars of the best
    # in-range NO ask. Prevents the strategy from sweeping deep into the
    # book when a market has a thin best level then a jump to a much
    # worse next level. STRATEGY.md §3 filter #9 requires <= 2c-4c;
    # 0.04 is the accepted default. Set to 1.0 to effectively disable.
    # Example: best_no_ask=0.82, max_ask_walk=0.04 -> only take asks
    # <= 0.86. Deeper asks (e.g., 0.90) are left on the book even if
    # otherwise eligible.
    max_ask_walk: float = 0.04

    # Market-wisdom cap: only trade when best_yes_ask <= this (equivalently,
    # best_no_bid >= 1 - max_yes_ask). At 0.22 this means we require the
    # market itself to agree the +1 bucket is unlikely before we fade it.
    # Canonical v2 rule (STRATEGY.md §3 filter #5). Drops backtest hit rate
    # from 98.7% (cap 0.50) to 100% (cap 0.22) at the cost of ~15% per-trade
    # edge. Set to 0.50 (or 1.0) to disable the market-wisdom gate and fall
    # back to the v1-style wider filter.
    max_yes_ask: float = 0.22

    # Per-market position cap in shares. Secondary safety; the primary
    # per-market risk control is `max_usd_per_market` below.
    shares_per_market: int = 110

    # Per-market USD cap on cumulative notional spent (sum of fill_qty *
    # fill_price, tracked as fills arrive). Hard risk control — even if
    # shares_per_market has room, we stop adding once we've spent this
    # much on the market. Default $30/market: at typical NO prices of
    # $0.80-$0.95, that's 30-38 shares per market, 2-3 markets/day, so
    # ~$60-90/day total notional. Combined with the ~$0.039/trade
    # expected edge, daily PnL expectation is ~$2-3 at this scale.
    # Scale up once realized tracks backtest.
    max_usd_per_market: float = 30.0

    # Minimum shares per IOC. Polymarket's per-market minimum is typically 5-15.
    min_order_shares: int = 5

    # Main loop tick cadence (seconds). Tighter = more reactive, also more
    # Python work. 0.5s is a comfortable balance.
    tick_interval_seconds: float = 0.5

    # How many days ahead to keep subscribed. 1 = today + tomorrow. Subscribing
    # ahead of time means when a market becomes today's tradeable, its book is
    # already warm. Nautilus auto-load handles the fetch transparently.
    lookahead_days: int = 1

    # Hard session-wide cap on IOC submissions. After this many submissions
    # (regardless of outcome — fill, partial, reject), the strategy flips the
    # circuit breaker and stops submitting. Default None = unlimited. Useful
    # for first-live smoke tests where you want exactly N orders on the wire.
    max_submissions_this_session: int | None = None

    # Minimum city-LOCAL hour (0-23) before the strategy may fire on a
    # given instrument. The gate is evaluated per-market against the
    # airport's local timezone (America/New_York for ATL/NYC/MIA, etc.).
    # Earlier local hours produce materially worse OOS — the 16-local
    # floor captures both HRRR full-peak-window coverage (fxx=6 from
    # inits 10-16 local) and intraday METAR absorption into the book
    # (winner / loser YES prices diverge around local 13-15). Backtest:
    # 13 local t=+1.06, 15 local t=+2.51, 16 local t=+3.67 (with cap 0.50)
    # or t=+7.70 (with canonical cap 0.22). See
    # notebooks/experiments/backtest-v3/consensus_optimal_sweep.py. Set
    # to 0 to disable (UTC behavior falls out from per-market tz lookup).
    min_entry_hour_local: int = 16

    # Bounded-window continuous take. Once a market first passes both the
    # local-hour gate AND the market-wisdom cap, the strategy keeps
    # lifting liquidity for this many minutes, then stops submitting
    # IOCs on that market even if gates are still passing. Prevents
    # edge degradation from accumulating fills deep into the afternoon
    # after the initial "post-METAR-absorption" window has passed.
    # The backtest models a single-shot entry at the first qualifying
    # hour; a bounded window preserves the extra fills from shallow
    # initial depth while bounding how far past the signal moment we
    # keep adding to position. Default 30 min. Set to 1440 (24h) to
    # effectively disable the window and mimic pre-window continuous
    # behavior.
    entry_window_minutes: int = 30


# -----------------------------------------------------------------------------
# State — everything the strategy knows about the world
# -----------------------------------------------------------------------------

@dataclass
class StrategyState:
    """Mutable state refreshed by each tick."""

    # Instruments we're subscribed to book deltas for. Grows as new qualifying
    # markets appear; shrinks when a market resolves (expiration_ns passes).
    subscribed: set[InstrumentId] = field(default_factory=set)

    # All markets we've discovered across the relevant date window
    # (yesterday UTC, today UTC, today + lookahead_days). Cached across
    # ticks and only refreshed when features/markets mtime or UTC date
    # changes. The `active` subset below is derived from this every tick
    # based on per-airport LOCAL date.
    discovered_markets: dict[InstrumentId, object] = field(default_factory=dict)

    # Subset of `discovered_markets` whose market_date equals THIS
    # AIRPORT'S local today. Take logic only fires on this set. Rebuilt
    # every tick (cheap dict walk) so it flips when Eastern airports cross
    # their local midnight even if UTC hasn't moved yet.
    active: dict[InstrumentId, object] = field(default_factory=dict)

    # Cached discovery inputs — skip the expensive discover() call when
    # nothing upstream has changed.
    discover_date: date | None = None
    active_features_mtime: float = 0.0
    active_markets_mtime: float = 0.0

    # Per-instrument position in shares (owned NO tokens). Updated on fills.
    positions: dict[InstrumentId, int] = field(default_factory=dict)

    # Per-instrument cumulative USD notional spent (sum of fill_qty *
    # fill_px). Used to enforce config.max_usd_per_market. Accumulated on
    # each on_order_filled event; never decremented (hold-to-resolution,
    # no intraday unwinds).
    usd_spent: dict[InstrumentId, float] = field(default_factory=dict)

    # Per-instrument: client_order_id of an IOC currently in flight.
    # Prevents double-submission while one is still resolving.
    pending: dict[InstrumentId, ClientOrderId] = field(default_factory=dict)

    # Per-instrument: timestamp (ns) when this market first passed
    # both the local-hour gate AND the market-wisdom cap. Used to
    # bound the entry window (config.entry_window_minutes). Once set,
    # sticks for the life of the session — a temporary gate failure
    # does not reset the window.
    first_eligible_ns: dict[InstrumentId, int] = field(default_factory=dict)

    # Per-instrument: set to True the first time we skip an otherwise-
    # takeable tick because the entry window has expired. Prevents log
    # spam — we log the close event once, not every 0.5s afterwards.
    window_closed: set[InstrumentId] = field(default_factory=set)

    # Per-instrument: set the first time we skip because the USD cap
    # has been reached. Log-once mirror of `window_closed` but for the
    # `max_usd_per_market` gate — kept separate so one gate's log
    # doesn't suppress the other's.
    usd_cap_hit: set[InstrumentId] = field(default_factory=set)

    # Throttle control for the expired-unsubscribe scan.
    last_expiry_check_ns: int = 0

    # Per-instrument rejection cooldown. When an IOC on instrument X is
    # rejected (e.g., geoblock, signature reject, venue maintenance), we
    # skip X for REJECT_COOLDOWN_NS before trying again. Prevents a tight
    # rejection loop from hammering the exchange API.
    last_reject_ns: dict[InstrumentId, int] = field(default_factory=dict)

    # Global circuit breaker: if we've accumulated CIRCUIT_BREAKER_THRESHOLD
    # consecutive rejects (across all markets) without any fill, flip this
    # flag and refuse to submit more orders. Manual restart required to reset.
    total_rejects_streak: int = 0
    circuit_broken: bool = False

    # Loop control.
    running: bool = False
    tick_count: int = 0

    # Total IOC submissions attempted this session. Used to honor
    # config.max_submissions_this_session for capped smoke tests.
    submissions_count: int = 0


# -----------------------------------------------------------------------------
# Strategy
# -----------------------------------------------------------------------------

class ConsensusFadeStrategy(Strategy):
    """Continuous polling loop with daily-market rollover + IOC takes."""

    def __init__(self, config: ConsensusFadeConfig) -> None:
        super().__init__(config)
        self._state = StrategyState()
        self._ledger: LedgerWriter | None = None
        self._snapshots: BookSnapshotWriter | None = None
        self._loop_task: asyncio.Task | None = None

    # --- Lifecycle --------------------------------------------------------

    def on_start(self) -> None:
        self._ledger = LedgerWriter()
        self._snapshots = BookSnapshotWriter()
        self._ledger.log(
            "session_start",
            instruments=list(self.config.instrument_ids),
            max_no_price=self.config.max_no_price,
            shares_per_market=self.config.shares_per_market,
            min_order_shares=self.config.min_order_shares,
            tick_interval_seconds=self.config.tick_interval_seconds,
            lookahead_days=self.config.lookahead_days,
            min_entry_hour_local=self.config.min_entry_hour_local,
            max_yes_ask=self.config.max_yes_ask,
        )
        if self.config.min_entry_hour_local > 0:
            self.log.info(
                f"entry gate: per-city local ≥ {self.config.min_entry_hour_local:02d}:00. "
                f"Each instrument's gate uses its airport tz "
                f"(CITY_TO_TZ) — submissions blocked until local clock ≥ floor.",
                color=LogColor.YELLOW,
            )
        self.log.info(
            f"market-wisdom cap: yes_ask ≤ {self.config.max_yes_ask} "
            f"(equivalently best_no_bid ≥ {1.0 - self.config.max_yes_ask:.2f})",
            color=LogColor.YELLOW,
        )
        self.log.info(
            f"ledger     → {self._ledger.dir_path}/YYYY-MM-DD.jsonl",
            color=LogColor.BLUE,
        )
        self.log.info(
            f"snapshots  → {self._snapshots.dir_path}/YYYY-MM-DD.jsonl",
            color=LogColor.BLUE,
        )

        # Seed subscriptions from the initial list. Nautilus auto-load covers
        # anything not yet in cache — any instrument fetched by
        # PolymarketInstrumentProviderConfig.load_ids is already cached; new
        # ones added later by the tick loop will be auto-loaded on subscribe.
        for iid_str in self.config.instrument_ids:
            iid = InstrumentId.from_str(iid_str)
            self.subscribe_order_book_deltas(
                iid, book_type=BookType.L2_MBP, depth=BOOK_DEPTH,
            )
            self._state.subscribed.add(iid)
        self.log.info(
            f"seeded {len(self._state.subscribed)} initial subscription(s); "
            f"rollover handles the rest",
            color=LogColor.BLUE,
        )

        # Kick off the continuous polling loop.
        self._state.running = True
        self._loop_task = asyncio.ensure_future(self._main_loop())

        # Schedule periodic book snapshots.
        self.clock.set_timer(
            name="book_snapshot",
            interval=BOOK_SNAPSHOT_INTERVAL,
            callback=self._on_snapshot_timer,
        )
        self.log.info(
            f"book-snapshot timer every {BOOK_SNAPSHOT_INTERVAL}",
            color=LogColor.BLUE,
        )

    def on_stop(self) -> None:
        self._state.running = False
        for order in self.cache.orders_open(strategy_id=self.id):
            self.cancel_order(order)
        if self._ledger is not None:
            self._ledger.log(
                "session_stop",
                tick_count=self._state.tick_count,
                subscribed_count=len(self._state.subscribed),
                positions={str(k): v for k, v in self._state.positions.items()},
            )
            self._ledger.close()
        if self._snapshots is not None:
            self._snapshots.close()

    # --- Main loop (literally `while True`) -------------------------------

    async def _main_loop(self) -> None:
        """Polling loop. Pulls state, decides whether to act, sleeps, repeats."""
        self.log.info(
            f"main loop started (tick every {self.config.tick_interval_seconds}s, "
            f"lookahead={self.config.lookahead_days} day(s))",
            color=LogColor.BLUE,
        )
        while self._state.running:
            self._state.tick_count += 1
            try:
                self._tick()
            except Exception as e:
                self.log.error(
                    f"tick #{self._state.tick_count} FAILED: {e!r}\n"
                    f"{traceback.format_exc()}",
                )
            await asyncio.sleep(self.config.tick_interval_seconds)
        self.log.info(
            f"main loop stopped after {self._state.tick_count} ticks",
            color=LogColor.BLUE,
        )

    def _tick(self) -> None:
        """One iteration of the loop: refresh state, act on each active market."""
        # (1) Sync the subscribed + active sets with the current data picture.
        #     Early-returns if nothing changed since last tick.
        self._refresh_subscribed_and_active()

        # (2) Unsubscribe resolved markets (throttled — runs every ~5min).
        self._unsubscribe_expired()

        # (3) For each currently-active market, consider taking liquidity.
        for iid in list(self._state.active.keys()):
            self._maybe_take(iid)

    # --- Data functions (cheap, called from _tick) ------------------------

    def _refresh_subscribed_and_active(self) -> None:
        """Two-step refresh, separated so active-set flipping is independent
        of discovery caching:

          (1) When UTC date or features/markets mtime has changed, re-run
              discover across a date window and update subscriptions +
              `discovered_markets` cache. Covers the expensive SQL work.

          (2) Every tick, rebuild `active` from `discovered_markets` by
              filtering for `market.market_date == airport_local_today`.
              Cheap dict walk; runs always so the active-set flips when
              any airport crosses its LOCAL midnight, not when UTC
              crosses midnight.

        Date window for (1) is `[today_utc-1, today_utc+lookahead]` so
        Eastern markets whose market_date is "yesterday UTC" (happens
        20:00-23:59 EDT daily) are still discoverable, and tomorrow's
        markets are warmed via the lookahead.
        """
        today_utc = datetime.now(UTC).date()
        features_mtime = (
            FEATURES_PATH.stat().st_mtime if FEATURES_PATH.exists() else 0.0
        )
        markets_mtime = (
            MARKETS_PATH.stat().st_mtime if MARKETS_PATH.exists() else 0.0
        )

        # (1) Refresh discovery cache when something upstream changed.
        discovery_stale = (
            today_utc != self._state.discover_date
            or features_mtime > self._state.active_features_mtime
            or markets_mtime > self._state.active_markets_mtime
        )
        if discovery_stale:
            # Lazy imports so this module is cheap to import from the CLI.
            from nautilus_trader.adapters.polymarket.common.symbol import (
                get_polymarket_instrument_id,
            )

            from consensus_fade_plus1.discover import discover_tradeable_markets

            discovered: dict[InstrumentId, object] = {}
            # yesterday_utc through today_utc + lookahead. yesterday_utc
            # covers Eastern markets after UTC midnight (20:00 EDT onwards)
            # whose market_date is the just-ended UTC day but the airport's
            # local day hasn't ended yet.
            for d_offset in range(-1, self.config.lookahead_days + 1):
                d = today_utc + timedelta(days=d_offset)
                try:
                    markets = discover_tradeable_markets(target_date=d, consensus_max=3.0)
                except FileNotFoundError:
                    continue  # markets.parquet missing — daemon hasn't run yet
                for m in markets:
                    iid = get_polymarket_instrument_id(m.condition_id, m.no_token_id)
                    if iid not in self._state.subscribed:
                        self.subscribe_order_book_deltas(
                            iid, book_type=BookType.L2_MBP, depth=BOOK_DEPTH,
                        )
                        self._state.subscribed.add(iid)
                        self._ledger.log(
                            "subscribed",
                            instrument_id=str(iid),
                            city=m.city,
                            market_date=str(m.market_date),
                            bucket=m.bucket_title,
                        )
                        self.log.info(
                            f"rollover+  subscribed {m.city} {m.market_date} "
                            f"({m.bucket_title})  [{iid}]",
                            color=LogColor.GREEN,
                        )
                    discovered[iid] = m
            self._state.discovered_markets = discovered
            self._state.discover_date = today_utc
            self._state.active_features_mtime = features_mtime
            self._state.active_markets_mtime = markets_mtime

        # (2) Rebuild active-set from discovered_markets, filtering on
        #     airport's CURRENT local date. Runs every tick so the flip
        #     at an airport's local midnight is reflected within one tick.
        new_active: dict[InstrumentId, object] = {}
        for iid, m in self._state.discovered_markets.items():
            tz_name = CITY_TO_TZ.get(m.city)
            if tz_name is None:
                continue  # unknown city — can't evaluate local date
            airport_today = datetime.now(ZoneInfo(tz_name)).date()
            if m.market_date == airport_today:
                new_active[iid] = m

        # Log diffs only when the set actually changes (it's stable across
        # most ticks — only moves at local midnight or when discovery runs).
        added = set(new_active) - set(self._state.active)
        removed = set(self._state.active) - set(new_active)
        for iid in added:
            m = new_active[iid]
            self.log.info(
                f"active+  {m.city} {m.market_date} ({m.bucket_title})",
                color=LogColor.GREEN,
            )
            self._ledger.log(
                "active_added",
                instrument_id=str(iid),
                city=m.city,
                market_date=str(m.market_date),
            )
        for iid in removed:
            self.log.info(f"active-  {iid}", color=LogColor.YELLOW)
            self._ledger.log("active_removed", instrument_id=str(iid))

        self._state.active = new_active

    def _unsubscribe_expired(self) -> None:
        """Scan subscribed instruments; unsubscribe any past their expiration.

        Throttled to every EXPIRY_CHECK_INTERVAL_NS (5 min).

        Grace period: Polymarket's Gamma API returns `end_date_iso` as a
        date string ("2026-04-20") which Nautilus's adapter parses as
        midnight UTC. But the actual market end_date is typically noon UTC
        that day (or later), and resolution can happen hours after. To
        avoid prematurely unsubscribing from markets still in their final
        trading window, we apply a 24h grace before considering expired.
        """
        now_ns = self.clock.timestamp_ns()
        if now_ns - self._state.last_expiry_check_ns < EXPIRY_CHECK_INTERVAL_NS:
            return
        self._state.last_expiry_check_ns = now_ns

        # 24h grace — see docstring
        grace_ns = 24 * 60 * 60 * 1_000_000_000

        for iid in list(self._state.subscribed):
            inst = self.cache.instrument(iid)
            if inst is None:
                continue
            if inst.expiration_ns and inst.expiration_ns + grace_ns < now_ns:
                self.unsubscribe_order_book_deltas(iid)
                self._state.subscribed.discard(iid)
                self._state.active.pop(iid, None)
                self._ledger.log(
                    "unsubscribed",
                    instrument_id=str(iid),
                    reason="expired",
                    expiration_ns=int(inst.expiration_ns),
                )
                self.log.info(
                    f"rollover-  unsubscribed {iid} (expired)",
                    color=LogColor.YELLOW,
                )

    def _takeable_shares(self, iid: InstrumentId) -> float:
        """Sum of ask qty at prices within the effective ceiling — the
        tighter of `max_no_price` (absolute) and `best_ask + max_ask_walk`
        (slippage cap from best). 0 if book empty or no qualifying asks."""
        book = self.cache.order_book(iid)
        if book is None:
            return 0.0
        total = 0.0
        best_ask: float | None = None
        for lvl in book.asks():
            px = float(lvl.price)
            if best_ask is None:
                best_ask = px
                if best_ask > self.config.max_no_price:
                    return 0.0  # even best ask is above absolute ceiling
            ceiling = min(self.config.max_no_price,
                          best_ask + self.config.max_ask_walk)
            if px > ceiling:
                break  # asks sorted ascending — rest are past slippage cap
            total += float(lvl.size())
        return total

    def _effective_ioc_price(self, iid: InstrumentId) -> float | None:
        """Submit price for the IOC: the tighter of max_no_price and
        best_ask + max_ask_walk. None if no best ask exists."""
        book = self.cache.order_book(iid)
        if book is None:
            return None
        for lvl in book.asks():
            best_ask = float(lvl.price)
            return min(self.config.max_no_price,
                       best_ask + self.config.max_ask_walk)
        return None

    # --- Action: submit an IOC when opportunity + room exist --------------

    def _best_no_bid(self, iid: InstrumentId) -> float | None:
        """Best NO bid = highest price buyers are paying for NO. If none,
        returns None. Equivalent to (1 - best_yes_ask) up to spread/fees."""
        book = self.cache.order_book(iid)
        if book is None:
            return None
        for lvl in book.bids():
            return float(lvl.price)  # first level is best
        return None

    def _local_hour_for(self, iid: InstrumentId) -> int | None:
        """City-local hour for this instrument's airport. None if city unknown."""
        market = self._state.active.get(iid)
        if market is None:
            return None
        tz_name = CITY_TO_TZ.get(market.city)
        if tz_name is None:
            return None
        return datetime.now(ZoneInfo(tz_name)).hour

    def _maybe_take(self, iid: InstrumentId) -> None:
        """If there's takeable liquidity and room under the cap, submit IOC BUY."""
        if self._state.circuit_broken:
            return  # global circuit breaker tripped
        # Per-instrument local-hour gate (STRATEGY.md §3 filter #5).
        min_hour = self.config.min_entry_hour_local
        if min_hour > 0:
            local_hour = self._local_hour_for(iid)
            if local_hour is None or local_hour < min_hour:
                return  # too early in this city's local day
        # Market-wisdom cap (STRATEGY.md §3 filter #5): require the current
        # book itself to agree the +1 bucket is unlikely before we fade it.
        # Evaluated as best NO bid >= 1 - max_yes_ask (since YES_ask + NO_bid <= 1).
        max_yes = self.config.max_yes_ask
        if max_yes < 1.0:
            no_bid = self._best_no_bid(iid)
            if no_bid is None or no_bid < (1.0 - max_yes):
                return  # market hasn't converged on "unlikely" yet

        # Entry-window check. Both of the above gates have now passed, so
        # this market IS currently eligible. Stamp the first-eligible
        # timestamp if not already set, then bail if we're past the
        # configured window. Bounded-window continuous take lets us pick
        # up multiple fills while the signal is fresh, but stops us from
        # accumulating position deep into the afternoon after edge decay.
        now_ns = self.clock.timestamp_ns()
        if iid not in self._state.first_eligible_ns:
            self._state.first_eligible_ns[iid] = now_ns
            market = self._state.active.get(iid)
            city = market.city if market is not None else "?"
            self.log.info(
                f"entry window opened  {city}  [{iid}]  "
                f"window={self.config.entry_window_minutes}m",
                color=LogColor.BLUE,
            )
            if self._ledger is not None:
                self._ledger.log(
                    "entry_window_opened",
                    instrument_id=str(iid),
                    city=city,
                    window_minutes=self.config.entry_window_minutes,
                )
        window_ns = self.config.entry_window_minutes * 60 * 1_000_000_000
        elapsed_ns = now_ns - self._state.first_eligible_ns[iid]
        if window_ns > 0 and elapsed_ns > window_ns:
            if iid not in self._state.window_closed:
                self._state.window_closed.add(iid)
                market = self._state.active.get(iid)
                city = market.city if market is not None else "?"
                pos = self._state.positions.get(iid, 0)
                self.log.info(
                    f"entry window closed  {city}  [{iid}]  "
                    f"elapsed={elapsed_ns // 60_000_000_000}m  final pos={pos}",
                    color=LogColor.YELLOW,
                )
                if self._ledger is not None:
                    self._ledger.log(
                        "entry_window_closed",
                        instrument_id=str(iid),
                        city=city,
                        elapsed_minutes=int(elapsed_ns // 60_000_000_000),
                        final_position=pos,
                    )
            return

        cap = self.config.max_submissions_this_session
        if cap is not None and self._state.submissions_count >= cap:
            # Session-wide submission cap reached — flip circuit so we stop
            # even if another instrument would have been takeable this tick.
            if not self._state.circuit_broken:
                self._state.circuit_broken = True
                self.log.info(
                    f"session submission cap reached "
                    f"({self._state.submissions_count}/{cap}) — stopping"
                )
                if self._ledger is not None:
                    self._ledger.log("session_cap_hit",
                                     submissions=self._state.submissions_count,
                                     cap=cap)
            return
        if iid in self._state.pending:
            return  # an IOC is still resolving on this market
        # Per-instrument cooldown after a rejection
        last_reject = self._state.last_reject_ns.get(iid, 0)
        if last_reject and self.clock.timestamp_ns() - last_reject < REJECT_COOLDOWN_NS:
            return
        pos = self._state.positions.get(iid, 0)
        room_shares = self.config.shares_per_market - pos
        if room_shares < self.config.min_order_shares:
            return  # per-market share cap hit

        # USD risk cap: convert remaining $ allowance into a share bound
        # using the configured max_no_price as a conservative per-share
        # cost (actual fills are usually cheaper).
        spent = self._state.usd_spent.get(iid, 0.0)
        usd_remaining = self.config.max_usd_per_market - spent
        max_shares_by_usd = int(usd_remaining / self.config.max_no_price)
        if max_shares_by_usd < self.config.min_order_shares:
            if iid not in self._state.usd_cap_hit:
                self._state.usd_cap_hit.add(iid)
                market = self._state.active.get(iid)
                city = market.city if market is not None else "?"
                self.log.info(
                    f"usd cap reached  {city}  [{iid}]  "
                    f"spent=${spent:.2f} cap=${self.config.max_usd_per_market:.2f}",
                    color=LogColor.YELLOW,
                )
                if self._ledger is not None:
                    self._ledger.log(
                        "usd_cap_hit",
                        instrument_id=str(iid),
                        city=city,
                        usd_spent=round(spent, 4),
                        usd_cap=self.config.max_usd_per_market,
                    )
            return

        takeable = self._takeable_shares(iid)
        if takeable < self.config.min_order_shares:
            return  # nothing in-range on the book right now

        qty = int(min(takeable, room_shares, max_shares_by_usd))
        self._submit_ioc_buy(iid, qty)

    def _submit_ioc_buy(self, iid: InstrumentId, qty: int) -> None:
        """Submit a limit BUY with IOC time-in-force. Price = tighter of
        max_no_price (absolute ceiling) and best_ask + max_ask_walk
        (slippage cap from best). Venue fills any ask <= our price, then
        cancels the rest — never rests on the book."""
        instrument = self.cache.instrument(iid)
        if instrument is None:
            return
        eff_price = self._effective_ioc_price(iid)
        if eff_price is None:
            return  # no asks — nothing to take
        price = self._snap_to_tick(eff_price, instrument)
        order = self.order_factory.limit(
            instrument_id=iid,
            order_side=OrderSide.BUY,
            quantity=Quantity.from_int(qty),
            price=price,
            time_in_force=TimeInForce.IOC,
        )
        # Track before submit so the next tick won't race another IOC on the
        # same instrument. Cleared when we see the order go terminal.
        self._state.pending[iid] = order.client_order_id
        self._state.submissions_count += 1
        self.submit_order(order)
        self.log.info(
            f"TAKE  {iid}  BUY {qty} @ {price} IOC",
            color=LogColor.CYAN,
        )

    # --- Event hooks → ledger + state updates -----------------------------

    def on_order_submitted(self, event: OrderSubmitted) -> None:
        self._ledger.log(
            "submitted",
            client_order_id=str(event.client_order_id),
            instrument_id=str(event.instrument_id),
        )

    def on_order_accepted(self, event: OrderAccepted) -> None:
        self._ledger.log(
            "accepted",
            client_order_id=str(event.client_order_id),
            venue_order_id=str(event.venue_order_id),
            instrument_id=str(event.instrument_id),
        )

    def on_order_filled(self, event: OrderFilled) -> None:
        # Any fill breaks the reject streak (exchange is clearly accepting orders).
        self._state.total_rejects_streak = 0
        qty = int(float(str(event.last_qty)))
        px = float(str(event.last_px))
        iid = event.instrument_id
        delta = qty if event.order_side == OrderSide.BUY else -qty
        self._state.positions[iid] = self._state.positions.get(iid, 0) + delta
        # Accumulate USD notional for the per-market USD cap. BUY only —
        # we never sell intraday (hold-to-resolution) so this is strictly
        # monotonic through a session.
        if event.order_side == OrderSide.BUY:
            self._state.usd_spent[iid] = self._state.usd_spent.get(iid, 0.0) + qty * px
        self._ledger.log(
            "filled",
            client_order_id=str(event.client_order_id),
            instrument_id=str(iid),
            side=str(event.order_side),
            last_qty=str(event.last_qty),
            last_px=str(event.last_px),
            commission=str(event.commission),
            position_after=self._state.positions[iid],
            usd_spent_after=round(self._state.usd_spent.get(iid, 0.0), 4),
        )
        self.log.info(
            f"FILL  {iid}  {event.last_qty} @ {event.last_px}  "
            f"fee={event.commission}  pos={self._state.positions[iid]}  "
            f"spent=${self._state.usd_spent.get(iid, 0.0):.2f}",
            color=LogColor.GREEN,
        )
        # IOC terminal state: either fully filled (FILLED) or partial-fill+cancel.
        # Clear pending when the order goes closed.
        order = self.cache.order(event.client_order_id)
        if order is not None and order.is_closed:
            self._state.pending.pop(iid, None)

    def on_order_canceled(self, event: OrderCanceled) -> None:
        self._state.pending.pop(event.instrument_id, None)
        self._ledger.log(
            "canceled",
            client_order_id=str(event.client_order_id),
            instrument_id=str(event.instrument_id),
        )

    def on_order_rejected(self, event: OrderRejected) -> None:
        self._state.pending.pop(event.instrument_id, None)
        # Cooldown on this instrument + global circuit breaker
        self._state.last_reject_ns[event.instrument_id] = self.clock.timestamp_ns()
        self._state.total_rejects_streak += 1
        tripped = (
            self._state.total_rejects_streak >= CIRCUIT_BREAKER_THRESHOLD
            and not self._state.circuit_broken
        )
        if tripped:
            self._state.circuit_broken = True
            self._ledger.log(
                "circuit_broken",
                consecutive_rejects=self._state.total_rejects_streak,
                threshold=CIRCUIT_BREAKER_THRESHOLD,
            )
            self.log.error(
                f"CIRCUIT BREAKER TRIPPED after "
                f"{self._state.total_rejects_streak} consecutive rejects — "
                f"no more orders will be submitted this session. Restart to reset.",
            )
        self._ledger.log(
            "rejected",
            client_order_id=str(event.client_order_id),
            instrument_id=str(event.instrument_id),
            reason=str(event.reason),
        )
        self.log.error(f"REJECTED  {event.instrument_id}  reason={event.reason}")

    # --- Book snapshot timer (unchanged) ----------------------------------

    def _on_snapshot_timer(self, event: TimeEvent) -> None:
        """Every BOOK_SNAPSHOT_INTERVAL, write top-N L2 snapshot of each subscribed book."""
        written = 0
        for iid in self._state.subscribed:
            book = self.cache.order_book(iid)
            if book is None:
                continue
            bids = [(float(lvl.price), float(lvl.size()))
                    for lvl in book.bids()[:BOOK_DEPTH]]
            asks = [(float(lvl.price), float(lvl.size()))
                    for lvl in book.asks()[:BOOK_DEPTH]]
            self._snapshots.snapshot(str(iid), bids, asks)
            written += 1
        self.log.info(
            f"book snapshot: wrote {written} instrument(s) to {self._snapshots.path}",
            color=LogColor.BLUE,
        )

    # --- Helpers ----------------------------------------------------------

    def _snap_to_tick(self, price_float: float, instrument) -> Price:
        """Round a float price down onto the market's tick grid."""
        tick = instrument.price_increment
        ticks = int(price_float / float(tick))
        return Price(ticks * float(tick), precision=tick.precision)
