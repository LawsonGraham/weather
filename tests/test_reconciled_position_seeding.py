"""Tests for BUGS.md B-002 fix: reconciled positions must credit _state.usd_spent.

Root cause (recap): Nautilus's system kernel runs startup reconciliation
BEFORE `_trader.start()` transitions strategies to RUNNING. Inferred
`OrderFilled` events synthesized during reconciliation are published, but
`Strategy.handle_event` drops events when the FSM is not RUNNING — so
`on_order_filled` never fires for pre-existing venue exposure. The fix is a
new `on_start`-time pass that reads `cache.positions_open()` and seeds
`_state.positions` + `_state.usd_spent` directly. The seed logic lives in a
pure module-level helper `compute_reconciled_position_seeds` so it can be
tested without a TradingNode.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from nautilus_trader.model.identifiers import InstrumentId

from consensus_fade_plus1.strategy import (
    ConsensusFadeStrategy,
    StrategyState,
    compute_reconciled_position_seeds,
)

# Realistic Polymarket NO-token instrument IDs (from weather-market-slugs/
# polymarket.csv — ATL 84-85°F on April 21 and April 22 2026).
IID_APR21 = InstrumentId.from_str(
    "0xe05da9bfb402684341bd78244202e48ce8c6b901978a3088a068a1b1debd4366-33197007773487548114221170146930750744800202020973896496586140961144564311363.POLYMARKET",
)
IID_APR22 = InstrumentId.from_str(
    "0xa94d24a516ed778acab3ebd26a46e2de9ac5af11afb136e3d99c9e22dea3e082-38690213508960445313652604493362140594109233503530982236380366175199809715492.POLYMARKET",
)


def _fake_position(iid, *, signed_qty, avg_px_open, strategy_id="EXTERNAL"):
    """Duck-typed stand-in for nautilus_trader.model.Position.

    The helper only reads .instrument_id, .signed_qty, .quantity (abs),
    .avg_px_open, and .strategy_id — everything else on Position is
    irrelevant to seeding.
    """
    return SimpleNamespace(
        instrument_id=iid,
        signed_qty=signed_qty,
        quantity=abs(signed_qty),
        avg_px_open=avg_px_open,
        strategy_id=strategy_id,
    )


# -----------------------------------------------------------------------------
# compute_reconciled_position_seeds — pure helper
# -----------------------------------------------------------------------------

class TestComputeReconciledPositionSeeds:
    """The pure helper. Most of the risk lives here."""

    def test_b002_repro_inferred_fill_with_zero_avg_px(self) -> None:
        """B-002 evidence: 17:54Z log showed 'Generated inferred OrderFilled
        ... last_qty=5.921800, last_px=0.00 USDC.e'. That propagates through
        Position.avg_px_open as 0.0. Fallback to max_no_price must kick in,
        producing usd_spent=5.92*0.93=5.50 — matching F-002's expected
        credit. With the bug, usd_spent was 0; with the fix, the fallback
        produces the safe overestimate."""
        position = _fake_position(IID_APR21, signed_qty=5.9218, avg_px_open=0.0)
        seeds = compute_reconciled_position_seeds([position], fallback_px=0.93)
        assert len(seeds) == 1
        iid, qty, effective_px, original_px = seeds[0]
        assert iid == IID_APR21
        assert qty == pytest.approx(5.9218)
        assert effective_px == 0.93  # fallback
        assert original_px == 0.0
        # USD credit if caller applies effective_px — this is what gets added
        # to _state.usd_spent. ~$5.50, consistent with BUGS.md F-002
        # verification line "fallback produced usd_spent += 5.92 * 0.93 = $5.50".
        assert qty * effective_px == pytest.approx(5.50, abs=0.02)

    def test_uses_real_avg_px_when_reconciliation_recovered_it(self) -> None:
        """Not all reconciled fills have last_px=0 — when the venue reports
        avg_px, Nautilus populates Position.avg_px_open correctly. Use that
        instead of the conservative fallback."""
        position = _fake_position(IID_APR22, signed_qty=10.0, avg_px_open=0.82)
        seeds = compute_reconciled_position_seeds([position], fallback_px=0.93)
        assert len(seeds) == 1
        _, _, effective_px, original_px = seeds[0]
        assert effective_px == 0.82
        assert original_px == 0.82

    def test_skips_short_positions(self) -> None:
        """Consensus-Fade +1 only opens LONG NO via BUY. A SHORT at startup
        is either a different strategy's exposure or a wallet anomaly —
        never credit its quantity against this strategy's caps."""
        short_pos = _fake_position(IID_APR21, signed_qty=-5.0, avg_px_open=0.20)
        seeds = compute_reconciled_position_seeds([short_pos], fallback_px=0.93)
        assert seeds == []

    def test_skips_flat_positions(self) -> None:
        """A flat (closed) position shouldn't consume budget either."""
        flat_pos = _fake_position(IID_APR21, signed_qty=0.0, avg_px_open=0.0)
        seeds = compute_reconciled_position_seeds([flat_pos], fallback_px=0.93)
        assert seeds == []

    def test_handles_empty_positions_list(self) -> None:
        """Cold-start (no prior exposure) is the common case. Must not raise."""
        assert compute_reconciled_position_seeds([], fallback_px=0.93) == []

    def test_multiple_positions_on_different_instruments(self) -> None:
        """Realistic restart scenario: yesterday's ATL Apr-21 position AND a
        new fill on an Apr-22 market both survive into reconciliation. Both
        should seed independently."""
        positions = [
            _fake_position(IID_APR21, signed_qty=5.9218, avg_px_open=0.0),
            _fake_position(IID_APR22, signed_qty=40.216215, avg_px_open=0.74),
        ]
        seeds = compute_reconciled_position_seeds(positions, fallback_px=0.93)
        assert len(seeds) == 2
        by_iid = {iid: (qty, eff_px, orig_px) for iid, qty, eff_px, orig_px in seeds}
        # Apr-21: avg_px=0 → fallback=0.93
        assert by_iid[IID_APR21] == (pytest.approx(5.9218), 0.93, 0.0)
        # Apr-22: avg_px=0.74 preserved
        assert by_iid[IID_APR22] == (pytest.approx(40.216215), 0.74, 0.74)


# -----------------------------------------------------------------------------
# Strategy._seed_state_from_reconciled_positions — wires pure helper into
# _state mutations + ledger/logger side effects
# -----------------------------------------------------------------------------

class _FakeCache:
    def __init__(self, positions):
        self._positions = positions

    def positions_open(self):
        return self._positions


class _FakeLog:
    def __init__(self):
        self.infos: list[str] = []

    def info(self, msg, **_kwargs):
        self.infos.append(msg)


class _FakeLedger:
    def __init__(self):
        self.entries: list[tuple[str, dict]] = []

    def log(self, event_type, **fields):
        self.entries.append((event_type, fields))


def _make_mock_strategy_self(positions, *, max_no_price=0.93, existing_state=None):
    """Produce a duck-typed 'self' that _seed_state_from_reconciled_positions
    can operate on. Avoids standing up a real TradingNode for a pure-logic test.

    The Strategy method only touches .cache, .config, ._state, ._ledger, .log
    — a SimpleNamespace matches perfectly.
    """
    state = existing_state if existing_state is not None else StrategyState()
    return SimpleNamespace(
        cache=_FakeCache(positions),
        config=SimpleNamespace(max_no_price=max_no_price),
        _state=state,
        _ledger=_FakeLedger(),
        log=_FakeLog(),
    )


class TestStrategySeedMethod:
    """End-to-end wiring check: method reads cache, mutates _state, logs."""

    def test_b002_end_to_end_reconciled_fill_credits_usd_spent(self) -> None:
        """B-002 root scenario: restart finds a 5.9218-share pre-existing
        position with avg_px=0 (from Nautilus's inferred fill). After
        on_start runs _seed_state_from_reconciled_positions, the
        strategy's _state.usd_spent[iid] is populated so the next TAKE's
        USD cap calculation uses the right baseline."""
        position = _fake_position(IID_APR21, signed_qty=5.9218, avg_px_open=0.0)
        mock_self = _make_mock_strategy_self([position], max_no_price=0.93)

        ConsensusFadeStrategy._seed_state_from_reconciled_positions(mock_self)

        # Position correctly seeded
        assert mock_self._state.positions[IID_APR21] == pytest.approx(5.9218)
        # USD credited via fallback (avg_px=0 → max_no_price=0.93)
        assert mock_self._state.usd_spent[IID_APR21] == pytest.approx(5.9218 * 0.93)

        # Ledger has a seed event with both original (0.0) and effective (0.93)
        assert any(
            t == "position_seeded_from_cache"
            and f["avg_px_open"] == 0.0
            and f["effective_px"] == 0.93
            for t, f in mock_self._ledger.entries
        )

    def test_additive_to_existing_state_so_on_start_is_idempotent_in_the_additive_sense(self) -> None:
        """If _state already has entries (e.g. partial state from elsewhere),
        the seed adds rather than overwriting. In practice _state is fresh
        at on_start time, so this is belt-and-braces — but matters if
        anyone ever calls seed twice."""
        existing = StrategyState()
        existing.positions[IID_APR21] = 2.0
        existing.usd_spent[IID_APR21] = 1.50

        position = _fake_position(IID_APR21, signed_qty=3.0, avg_px_open=0.80)
        mock_self = _make_mock_strategy_self(
            [position], max_no_price=0.93, existing_state=existing,
        )

        ConsensusFadeStrategy._seed_state_from_reconciled_positions(mock_self)

        assert mock_self._state.positions[IID_APR21] == pytest.approx(5.0)
        assert mock_self._state.usd_spent[IID_APR21] == pytest.approx(1.50 + 3.0 * 0.80)

    def test_usd_cap_math_against_b002_scenario(self) -> None:
        """The canonical B-002 complaint: with the bug, max_shares_by_usd
        at the next TAKE was 32 (= int(30/0.93)). With the fix and a
        reconciled 5.92-share fill, max_shares_by_usd should drop to
        int((30 - 5.50) / 0.93) = int(26.34) = 26.

        NB: in reality the two are on different instruments (Apr-21 vs
        Apr-22 markets have different condition_ids). This test exercises
        the hypothetical same-market restart case — the more concerning
        mode per BUGS.md's impact line ('per-market USD cap is effectively
        fresh budget per session restart'). It's what the fix protects."""
        position = _fake_position(IID_APR21, signed_qty=5.9218, avg_px_open=0.0)
        mock_self = _make_mock_strategy_self([position], max_no_price=0.93)

        ConsensusFadeStrategy._seed_state_from_reconciled_positions(mock_self)

        # Now replay the strategy's USD-cap arithmetic (from _maybe_take)
        max_usd_per_market = 30.0
        spent = mock_self._state.usd_spent[IID_APR21]
        usd_remaining = max_usd_per_market - spent
        max_shares_by_usd = int(usd_remaining / 0.93)

        assert spent == pytest.approx(5.50, abs=0.02)
        assert max_shares_by_usd == 26  # was 32 with the bug
