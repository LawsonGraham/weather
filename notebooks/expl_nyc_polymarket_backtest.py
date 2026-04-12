"""NYC Polymarket daily-temperature — intraday sniping backtest.

Hypothesis:
    Polymarket daily-temperature NYC markets stay live throughout the target
    local day (fills continue well after `end_date`). Meanwhile, LGA temperature
    evolves minute-by-minute. When the running daily max crosses a strike
    threshold, `X°F or higher` strikes become deterministically YES and `X°F or
    below` strikes become deterministically NO. If the order book is slow to
    reprice after the threshold crossing, there's a snipe.

Setup:
    • Ground truth: IEM ASOS 1-min LGA `tmpf` → running max per local NY day.
    • Market state: Polymarket processed `prices` parquet — per-second
      forward-filled `yes_price`, covering every slug from creation to close.
    • Scope: closed NYC `Daily Temperature` markets, "X°F or higher" and
      "X°F or below" ladder rungs only (the two cleanest deterministic cases).

For each (slug, local_day) we compute:
    • lock_time_utc  — first 1-min obs where running max crosses the threshold
    • price_at_lock  — yes_price at lock_time (stale, pre-reaction)
    • price_{5,15,60}m_post — reaction trajectory
    • reaction_latency_s — seconds until the market moves ≥ 50% toward truth
    • snipe_edge_usd_per_share — the gap between stale price and eventual payoff

Usage:
    uv run python notebooks/expl_nyc_polymarket_backtest.py
"""
from __future__ import annotations

import duckdb
import pandas as pd

pd.set_option("display.max_columns", None)
pd.set_option("display.width", 260)
pd.set_option("display.max_rows", 200)

MARKETS = "data/processed/polymarket_weather/markets.parquet"
PRICES = "data/processed/polymarket_weather/prices/**/*.parquet"
ASOS1 = "data/raw/iem_asos_1min/LGA/*.csv"
METAR = "data/processed/iem_metar/LGA/*.parquet"


def build_views(con: duckdb.DuckDBPyConnection) -> None:
    con.execute("SET TimeZone = 'UTC'")

    # 1-min LGA with running max computed per local NY day.
    # Running max is strictly causal (rows up to and including current).
    # NB: valid(UTC) is read as naive TIMESTAMP — must attach 'UTC' first before
    # converting to NY wall clock, else the conversion direction is reversed.
    con.execute(f"""
        CREATE OR REPLACE VIEW lga_1min AS
        SELECT
            ("valid(UTC)" AT TIME ZONE 'UTC')                                           AS ts_utc,
            (("valid(UTC)" AT TIME ZONE 'UTC') AT TIME ZONE 'America/New_York')         AS ts_local,
            CAST((("valid(UTC)" AT TIME ZONE 'UTC') AT TIME ZONE 'America/New_York') AS DATE) AS local_date,
            TRY_CAST(tmpf AS DOUBLE)                                                    AS tmpf
        FROM read_csv_auto('{ASOS1}', union_by_name=true)
        WHERE station = 'LGA'
          AND TRY_CAST(tmpf AS DOUBLE) IS NOT NULL
    """)

    con.execute("""
        CREATE OR REPLACE VIEW lga_running AS
        SELECT
            ts_utc,
            ts_local,
            local_date,
            tmpf,
            MAX(tmpf) OVER (PARTITION BY local_date ORDER BY ts_utc
                            ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS running_max,
            MIN(tmpf) OVER (PARTITION BY local_date ORDER BY ts_utc
                            ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS running_min
        FROM lga_1min
    """)

    # All closed NYC Daily Temperature strikes, classified by kind.
    # kind in {'or_higher', 'or_below', 'range'}.
    # For 'range' strikes, lo_f..hi_f are inclusive bounds (e.g., "54-55°F" → 54..55).
    con.execute(f"""
        CREATE OR REPLACE VIEW nyc_ladder AS
        SELECT
            slug,
            question,
            group_item_title AS strike,
            CASE
                WHEN group_item_title ILIKE '%or higher%' THEN 'or_higher'
                WHEN group_item_title ILIKE '%or below%'  THEN 'or_below'
                ELSE 'range'
            END AS kind,
            CASE
                WHEN group_item_title ILIKE '%or higher%'
                    THEN CAST(regexp_extract(group_item_title, '(-?\\d+)', 1) AS INT)
                WHEN group_item_title ILIKE '%or below%'
                    THEN CAST(regexp_extract(group_item_title, '(-?\\d+)', 1) AS INT)
                ELSE CAST(regexp_extract(group_item_title, '(-?\\d+)-', 1) AS INT)
            END AS lo_f,
            CASE
                WHEN group_item_title ILIKE '%or higher%'
                    THEN CAST(regexp_extract(group_item_title, '(-?\\d+)', 1) AS INT)
                WHEN group_item_title ILIKE '%or below%'
                    THEN CAST(regexp_extract(group_item_title, '(-?\\d+)', 1) AS INT)
                ELSE CAST(regexp_extract(group_item_title, '-(-?\\d+)', 1) AS INT)
            END AS hi_f,
            last_trade_price AS p_final_snapshot,
            end_date,
            closed_time,
            CAST((end_date AT TIME ZONE 'America/New_York') AS DATE) AS local_day,
            volume_num, liquidity_num,
            best_bid, best_ask, spread,
            order_price_min_tick_size AS tick
        FROM '{MARKETS}'
        WHERE city='New York City'
          AND weather_tags ILIKE '%Daily Temperature%'
          AND closed
    """)

    # Lock events: first 1-min obs where the strike becomes deterministically
    # resolved. Two lock types per strike kind:
    #   or_higher X:   YES-lock when running_max >= X
    #   or_below  X:   NO-lock  when running_max >  X
    #   range [X..Y]:  NO-lock  when running_max >  Y   (knock-out)
    #                  (knock-in at running_max >= X is not deterministic — still
    #                   uncertain between YES and NO — so we don't treat it as a lock)
    #
    # We require the cross to be SUSTAINED for SUSTAIN_MINUTES minutes (window
    # containing a continuous block at or above threshold). This rejects the
    # 1-minute sensor spikes that the market correctly ignores.
    con.execute("""
        CREATE OR REPLACE VIEW lock_events AS
        WITH joined AS (
            SELECT
                m.slug,
                m.strike,
                m.kind,
                m.lo_f, m.hi_f,
                m.local_day,
                r.ts_utc,
                r.tmpf,
                r.running_max,
                -- Effective lock threshold per kind
                CASE
                    WHEN m.kind='or_higher' AND r.running_max >= m.lo_f       THEN 1
                    WHEN m.kind='or_below'  AND r.running_max >  m.hi_f       THEN 0
                    WHEN m.kind='range'     AND r.running_max >  m.hi_f       THEN 0
                END AS locked_resolution,
                -- Sustain counter: count how many minutes in a row the raw tmpf
                -- (not running_max) has been at or past the lock boundary. This
                -- is the "sustained" check: for or_higher lo=X, count minutes
                -- with tmpf>=X; for or_below hi=X or range hi=X, count minutes
                -- with tmpf>X.
                CASE
                    WHEN m.kind='or_higher' AND r.tmpf >= m.lo_f THEN 1
                    WHEN m.kind='or_below'  AND r.tmpf >  m.hi_f THEN 1
                    WHEN m.kind='range'     AND r.tmpf >  m.hi_f THEN 1
                    ELSE 0
                END AS at_boundary
            FROM nyc_ladder m
            JOIN lga_running r ON r.local_date = m.local_day
        ),
        sustained AS (
            SELECT *,
                   SUM(at_boundary) OVER (
                       PARTITION BY slug ORDER BY ts_utc
                       ROWS BETWEEN 2 PRECEDING AND CURRENT ROW
                   ) AS sustained_3m
            FROM joined
        ),
        first_lock AS (
            SELECT slug, strike, kind, lo_f, hi_f, local_day, locked_resolution,
                   ts_utc, tmpf, running_max,
                   ROW_NUMBER() OVER (PARTITION BY slug ORDER BY ts_utc) AS rn
            FROM sustained
            WHERE locked_resolution IS NOT NULL
              AND sustained_3m >= 3
        )
        SELECT slug, strike, kind, lo_f, hi_f, local_day, locked_resolution,
               ts_utc          AS lock_ts_utc,
               tmpf            AS tmpf_at_lock,
               running_max     AS running_max_at_lock
        FROM first_lock
        WHERE rn = 1
    """)

    # METAR daily max — primary truth for realized outcomes. METAR hourly `tmpf`
    # plus `max_temp_6hr_c` from the 00/06/12/18Z synoptic reports. Rowwise max
    # in F, then groupby local-NY day. Complete coverage for every day in the
    # market window unlike the gappy 1-min archive.
    con.execute(f"""
        CREATE OR REPLACE VIEW metar_daily AS
        WITH m AS (
            SELECT
                CAST((valid AT TIME ZONE 'America/New_York') AS DATE) AS local_date,
                GREATEST(COALESCE(tmpf, -999),
                         COALESCE(max_temp_6hr_c * 9.0/5.0 + 32.0, -999)) AS tmpf_effective
            FROM '{METAR}'
            WHERE station = 'LGA'
        )
        SELECT
            local_date,
            MAX(tmpf_effective) AS day_max_raw,
            ROUND(MAX(tmpf_effective))::INT AS day_max_whole,
            COUNT(*) AS n_obs
        FROM m WHERE tmpf_effective > -900
        GROUP BY 1
    """)

    # Realized outcomes via METAR. Preferred truth because METAR is gap-free
    # in the relevant window; ASOS 1-min stays as a cross-check in the snipe
    # analysis since directional running-max evidence is still valid even with
    # 1-min gaps.
    con.execute("""
        CREATE OR REPLACE VIEW realized_daily AS
        SELECT
            m.slug, m.strike, m.kind, m.lo_f, m.hi_f, m.local_day,
            md.day_max_raw,
            md.day_max_whole,
            md.n_obs,
            CASE
                WHEN md.day_max_whole IS NULL THEN NULL
                WHEN m.kind='or_higher' AND md.day_max_whole >= m.lo_f THEN 1
                WHEN m.kind='or_higher' THEN 0
                WHEN m.kind='or_below'  AND md.day_max_whole <= m.hi_f THEN 1
                WHEN m.kind='or_below'  THEN 0
                WHEN m.kind='range'     AND md.day_max_whole BETWEEN m.lo_f AND m.hi_f THEN 1
                WHEN m.kind='range'     THEN 0
            END AS realized_yes
        FROM nyc_ladder m
        LEFT JOIN metar_daily md ON md.local_date = m.local_day
    """)


def make_snipes_table(con: duckdb.DuckDBPyConnection) -> None:
    """Per-event snipe analysis: snapshot yes_price at lock, then 5/15/60 min later."""

    # Only compute lock_price_window for slugs that actually have lock events — keeps
    # the giant prices parquet scan bounded.
    con.execute("""
        CREATE OR REPLACE TEMP TABLE locks_with_prices AS
        WITH l AS (
            SELECT * FROM lock_events
        ),
        pre AS (
            -- Last real price at or before lock_ts_utc
            SELECT l.slug, l.strike, l.kind, l.lo_f, l.hi_f, l.local_day, l.locked_resolution,
                   l.lock_ts_utc, l.tmpf_at_lock, l.running_max_at_lock,
                   p.timestamp AS price_ts,
                   p.yes_price
            FROM l
            JOIN 'data/processed/polymarket_weather/prices/**/*.parquet' p
              ON p.slug = l.slug
             AND p.timestamp <= l.lock_ts_utc
            QUALIFY ROW_NUMBER() OVER (PARTITION BY l.slug ORDER BY p.timestamp DESC) = 1
        ),
        post5 AS (
            SELECT l.slug, p.yes_price, p.timestamp AS t
            FROM l
            JOIN 'data/processed/polymarket_weather/prices/**/*.parquet' p
              ON p.slug = l.slug
             AND p.timestamp >= l.lock_ts_utc + INTERVAL '5 minute'
            QUALIFY ROW_NUMBER() OVER (PARTITION BY l.slug ORDER BY p.timestamp) = 1
        ),
        post15 AS (
            SELECT l.slug, p.yes_price
            FROM l
            JOIN 'data/processed/polymarket_weather/prices/**/*.parquet' p
              ON p.slug = l.slug
             AND p.timestamp >= l.lock_ts_utc + INTERVAL '15 minute'
            QUALIFY ROW_NUMBER() OVER (PARTITION BY l.slug ORDER BY p.timestamp) = 1
        ),
        post60 AS (
            SELECT l.slug, p.yes_price
            FROM l
            JOIN 'data/processed/polymarket_weather/prices/**/*.parquet' p
              ON p.slug = l.slug
             AND p.timestamp >= l.lock_ts_utc + INTERVAL '60 minute'
            QUALIFY ROW_NUMBER() OVER (PARTITION BY l.slug ORDER BY p.timestamp) = 1
        )
        SELECT
            pre.slug, pre.strike, pre.kind, pre.lo_f, pre.hi_f, pre.local_day,
            pre.locked_resolution,
            pre.lock_ts_utc,
            pre.tmpf_at_lock,
            pre.running_max_at_lock,
            pre.price_ts     AS preclock_price_ts,
            pre.yes_price    AS p_at_lock,
            post5.yes_price  AS p_post_5m,
            post5.t          AS post_5m_ts,
            post15.yes_price AS p_post_15m,
            post60.yes_price AS p_post_60m,
            -- "Fair" value after lock
            CASE WHEN pre.locked_resolution=1 THEN 1.0 ELSE 0.0 END AS fair_value,
            -- Edge = fair - stale_price. For YES-lock with stale low price → positive edge buying YES.
            -- For NO-lock with stale high price → positive edge buying NO.
            CASE
                WHEN pre.locked_resolution=1 THEN 1.0 - pre.yes_price
                ELSE pre.yes_price - 0.0
            END AS snipe_edge
        FROM pre
        LEFT JOIN post5  USING (slug)
        LEFT JOIN post15 USING (slug)
        LEFT JOIN post60 USING (slug)
    """)


def print_coverage(con: duckdb.DuckDBPyConnection) -> None:
    print("\n=== LOCK EVENT COVERAGE ===")
    print(con.execute("""
        SELECT
            (SELECT COUNT(*) FROM nyc_ladder)                  AS n_ladder_markets,
            (SELECT COUNT(*) FROM lock_events)                 AS n_with_lock_observed,
            (SELECT COUNT(DISTINCT local_day) FROM lock_events) AS n_days_with_locks,
            (SELECT MIN(local_day) FROM lock_events)            AS first_day,
            (SELECT MAX(local_day) FROM lock_events)            AS last_day
    """).df())
    print("\nLocks by kind:")
    print(con.execute("""
        SELECT kind, locked_resolution, COUNT(*) AS n
        FROM lock_events GROUP BY 1,2 ORDER BY 1,2
    """).df())


def print_snipes(con: duckdb.DuckDBPyConnection) -> None:
    print("\n=== TOP 25 SNIPE OPPORTUNITIES (largest price-vs-fair gap at lock moment) ===")
    print(con.execute("""
        SELECT
            local_day,
            strike,
            kind,
            ROUND(tmpf_at_lock, 1)       AS temp_at_lock,
            ROUND(running_max_at_lock,1) AS rmax_at_lock,
            lock_ts_utc,
            ROUND(p_at_lock, 3)          AS p_stale,
            ROUND(fair_value, 2)         AS fair,
            ROUND(p_post_5m, 3)          AS p_5m,
            ROUND(p_post_15m, 3)         AS p_15m,
            ROUND(p_post_60m, 3)         AS p_60m,
            ROUND(snipe_edge, 3)         AS edge
        FROM locks_with_prices
        WHERE snipe_edge IS NOT NULL
        ORDER BY snipe_edge DESC
        LIMIT 25
    """).df())


def print_reaction_stats(con: duckdb.DuckDBPyConnection) -> None:
    print("\n=== REACTION STATS (how much does price close the gap over time?) ===")
    # Fraction of the gap closed at 5m / 15m / 60m
    print(con.execute("""
        WITH s AS (
            SELECT
                snipe_edge,
                -- Fraction of snipe edge closed = (p_t - p_at_lock) / (fair - p_at_lock) for YES lock
                -- or (p_at_lock - p_t) / (p_at_lock - 0) for NO lock.
                CASE WHEN locked_resolution=1 AND (fair_value - p_at_lock) > 1e-6
                     THEN (p_post_5m  - p_at_lock) / (fair_value - p_at_lock)
                     WHEN locked_resolution=0 AND (p_at_lock - fair_value) > 1e-6
                     THEN (p_at_lock - p_post_5m ) / (p_at_lock - fair_value)
                END AS frac_5m,
                CASE WHEN locked_resolution=1 AND (fair_value - p_at_lock) > 1e-6
                     THEN (p_post_15m - p_at_lock) / (fair_value - p_at_lock)
                     WHEN locked_resolution=0 AND (p_at_lock - fair_value) > 1e-6
                     THEN (p_at_lock - p_post_15m) / (p_at_lock - fair_value)
                END AS frac_15m,
                CASE WHEN locked_resolution=1 AND (fair_value - p_at_lock) > 1e-6
                     THEN (p_post_60m - p_at_lock) / (fair_value - p_at_lock)
                     WHEN locked_resolution=0 AND (p_at_lock - fair_value) > 1e-6
                     THEN (p_at_lock - p_post_60m) / (p_at_lock - fair_value)
                END AS frac_60m
            FROM locks_with_prices
            WHERE snipe_edge IS NOT NULL
        )
        SELECT
            COUNT(*)                                             AS n_events,
            ROUND(AVG(snipe_edge), 3)                            AS mean_edge,
            ROUND(AVG(frac_5m ), 3)                              AS mean_frac_closed_5m,
            ROUND(AVG(frac_15m), 3)                              AS mean_frac_closed_15m,
            ROUND(AVG(frac_60m), 3)                              AS mean_frac_closed_60m,
            ROUND(QUANTILE_CONT(frac_5m , 0.5), 3)               AS med_frac_5m,
            ROUND(QUANTILE_CONT(frac_15m, 0.5), 3)               AS med_frac_15m,
            ROUND(QUANTILE_CONT(frac_60m, 0.5), 3)               AS med_frac_60m
        FROM s
    """).df())


def print_materiality_filter(con: duckdb.DuckDBPyConnection) -> None:
    print("\n=== MATERIAL SNIPES (edge >= 10¢) — the actually-tradeable set ===")
    print(con.execute("""
        SELECT
            local_day, strike, kind,
            ROUND(p_at_lock,3) AS p_stale,
            fair_value AS fair,
            ROUND(p_post_5m,3) AS p_5m,
            ROUND(p_post_15m,3) AS p_15m,
            ROUND(p_post_60m,3) AS p_60m,
            ROUND(snipe_edge,3) AS edge
        FROM locks_with_prices
        WHERE snipe_edge >= 0.10
        ORDER BY local_day, lock_ts_utc
    """).df())

    print("\n=== MATERIAL SNIPE SUMMARY ===")
    print(con.execute("""
        SELECT
            COUNT(*) AS n_material_snipes,
            ROUND(AVG(snipe_edge),3) AS mean_edge,
            ROUND(SUM(snipe_edge),2) AS sum_edge_per_share_stack,
            COUNT(DISTINCT local_day) AS n_days
        FROM locks_with_prices
        WHERE snipe_edge >= 0.10
    """).df())


def build_preclose_curve(con: duckdb.DuckDBPyConnection) -> None:
    """Market-internal efficiency curve — for each closed strike, sample the
    yes_price at fixed UTC hours on the TARGET DAY, then Brier-score each
    snapshot against the realized outcome from 1-min LGA truth.

    Anchors are chosen to match the weather day's arc (all UTC, EDT is UTC-4):
        t_overnight  = target_day 09:00 UTC  (05:00 EDT, before sunrise)
        t_morning    = target_day 13:00 UTC  (09:00 EDT)
        t_noon       = target_day 17:00 UTC  (13:00 EDT, prime climbing window)
        t_afternoon  = target_day 20:00 UTC  (16:00 EDT, typical daily max time)
        t_final      = target_day 23:30 UTC  (19:30 EDT, after max is locked in)

    end_date on Polymarket is ~07:00 EDT which is BEFORE the weather day arc
    (proven by seeing 99¢ markets that flipped to 0 during the day), so it is
    NOT a valid "close" anchor. We re-anchor to the actual day.
    """
    con.execute("""
        CREATE OR REPLACE TEMP TABLE preclose_curve AS
        WITH picks AS (
            SELECT
                m.slug, m.strike, m.kind, m.lo_f, m.hi_f, m.local_day,
                CAST(m.local_day AS TIMESTAMPTZ) + INTERVAL '09 hour' AS t_overnight,
                CAST(m.local_day AS TIMESTAMPTZ) + INTERVAL '13 hour' AS t_morning,
                CAST(m.local_day AS TIMESTAMPTZ) + INTERVAL '17 hour' AS t_noon,
                CAST(m.local_day AS TIMESTAMPTZ) + INTERVAL '20 hour' AS t_afternoon,
                CAST(m.local_day AS TIMESTAMPTZ) + INTERVAL '23 hour 30 minute' AS t_final,
                r.realized_yes,
                r.day_max_whole
            FROM nyc_ladder m
            JOIN realized_daily r USING (slug)
            WHERE r.realized_yes IS NOT NULL
        )
        SELECT
            pk.slug, pk.strike, pk.kind, pk.lo_f, pk.hi_f,
            pk.local_day, pk.realized_yes, pk.day_max_whole,
            (SELECT yes_price FROM 'data/processed/polymarket_weather/prices/**/*.parquet' p
             WHERE p.slug = pk.slug AND p.timestamp <= pk.t_overnight
             ORDER BY p.timestamp DESC LIMIT 1) AS p_overnight,
            (SELECT yes_price FROM 'data/processed/polymarket_weather/prices/**/*.parquet' p
             WHERE p.slug = pk.slug AND p.timestamp <= pk.t_morning
             ORDER BY p.timestamp DESC LIMIT 1) AS p_morning,
            (SELECT yes_price FROM 'data/processed/polymarket_weather/prices/**/*.parquet' p
             WHERE p.slug = pk.slug AND p.timestamp <= pk.t_noon
             ORDER BY p.timestamp DESC LIMIT 1) AS p_noon,
            (SELECT yes_price FROM 'data/processed/polymarket_weather/prices/**/*.parquet' p
             WHERE p.slug = pk.slug AND p.timestamp <= pk.t_afternoon
             ORDER BY p.timestamp DESC LIMIT 1) AS p_afternoon,
            (SELECT yes_price FROM 'data/processed/polymarket_weather/prices/**/*.parquet' p
             WHERE p.slug = pk.slug AND p.timestamp <= pk.t_final
             ORDER BY p.timestamp DESC LIMIT 1) AS p_final
        FROM picks pk
    """)


def print_preclose_curve(con: duckdb.DuckDBPyConnection) -> None:
    print("\n=== MARKET EFFICIENCY CURVE (Brier score across the target day, all UTC) ===")
    print("    Anchor hours: 09Z=05EDT overnight | 13Z=09EDT morning | 17Z=13EDT noon")
    print("                  20Z=16EDT afternoon | 23:30Z=19:30EDT end-of-day")
    print("    Smaller brier = tighter price. Gap between anchors = where market learned.")
    print(con.execute("""
        SELECT
            COUNT(*) AS n,
            ROUND(AVG(realized_yes), 4) AS base_rate,
            ROUND(AVG((p_overnight -realized_yes)*(p_overnight -realized_yes)), 5) AS brier_overnight,
            ROUND(AVG((p_morning   -realized_yes)*(p_morning   -realized_yes)), 5) AS brier_morning,
            ROUND(AVG((p_noon      -realized_yes)*(p_noon      -realized_yes)), 5) AS brier_noon,
            ROUND(AVG((p_afternoon -realized_yes)*(p_afternoon -realized_yes)), 5) AS brier_afternoon,
            ROUND(AVG((p_final     -realized_yes)*(p_final     -realized_yes)), 5) AS brier_final
        FROM preclose_curve
        WHERE p_overnight IS NOT NULL AND p_final IS NOT NULL
    """).df())

    print("\n=== AVERAGE ABSOLUTE DRIFT BETWEEN ANCHORS ===")
    print("    How much does the market move between each pair of snapshots?")
    print(con.execute("""
        SELECT
            ROUND(AVG(ABS(p_morning   - p_overnight)), 4) AS drift_over_to_morn,
            ROUND(AVG(ABS(p_noon      - p_morning)),   4) AS drift_morn_to_noon,
            ROUND(AVG(ABS(p_afternoon - p_noon)),      4) AS drift_noon_to_aft,
            ROUND(AVG(ABS(p_final     - p_afternoon)), 4) AS drift_aft_to_final
        FROM preclose_curve
        WHERE p_overnight IS NOT NULL AND p_final IS NOT NULL
    """).df())

    print("\n=== BY STRIKE KIND — where does uncertainty resolve? ===")
    print(con.execute("""
        SELECT
            kind,
            COUNT(*) AS n,
            ROUND(AVG(realized_yes), 3) AS base_rate,
            ROUND(AVG((p_overnight -realized_yes)*(p_overnight -realized_yes)), 4) AS brier_over,
            ROUND(AVG((p_morning   -realized_yes)*(p_morning   -realized_yes)), 4) AS brier_morn,
            ROUND(AVG((p_noon      -realized_yes)*(p_noon      -realized_yes)), 4) AS brier_noon,
            ROUND(AVG((p_afternoon -realized_yes)*(p_afternoon -realized_yes)), 4) AS brier_aft,
            ROUND(AVG((p_final     -realized_yes)*(p_final     -realized_yes)), 4) AS brier_final
        FROM preclose_curve
        WHERE p_overnight IS NOT NULL AND p_final IS NOT NULL
        GROUP BY kind ORDER BY kind
    """).df())

    print("\n=== BIGGEST MORNING→AFTERNOON REVISIONS (the alpha that a live model would have captured) ===")
    print(con.execute("""
        SELECT
            local_day, strike, kind, day_max_whole,
            ROUND(p_overnight, 3) AS p_over,
            ROUND(p_morning, 3)   AS p_morn,
            ROUND(p_noon, 3)      AS p_noon,
            ROUND(p_afternoon, 3) AS p_aft,
            ROUND(p_final, 3)     AS p_end,
            realized_yes AS y,
            ROUND(ABS(p_morning - p_afternoon), 3) AS morn_to_aft_drift
        FROM preclose_curve
        WHERE p_morning IS NOT NULL AND p_afternoon IS NOT NULL
        ORDER BY morn_to_aft_drift DESC
        LIMIT 20
    """).df())

    print("\n=== DAILY LADDER RELIABILITY — was the winning strike already the favorite? ===")
    print("    Rank at each anchor for the strike that ultimately resolved YES.")
    print(con.execute("""
        WITH r AS (
            SELECT local_day, strike, realized_yes,
                   RANK() OVER (PARTITION BY local_day ORDER BY p_overnight DESC) AS rk_over,
                   RANK() OVER (PARTITION BY local_day ORDER BY p_morning   DESC) AS rk_morn,
                   RANK() OVER (PARTITION BY local_day ORDER BY p_noon      DESC) AS rk_noon,
                   RANK() OVER (PARTITION BY local_day ORDER BY p_afternoon DESC) AS rk_aft
            FROM preclose_curve
            WHERE p_overnight IS NOT NULL
        )
        SELECT
            COUNT(*) FILTER (WHERE realized_yes=1 AND rk_over=1) AS fav_right_overnight,
            COUNT(*) FILTER (WHERE realized_yes=1 AND rk_morn=1) AS fav_right_morning,
            COUNT(*) FILTER (WHERE realized_yes=1 AND rk_noon=1) AS fav_right_noon,
            COUNT(*) FILTER (WHERE realized_yes=1 AND rk_aft=1)  AS fav_right_afternoon,
            COUNT(DISTINCT local_day) AS n_days
        FROM r
    """).df())


def main() -> None:
    con = duckdb.connect()
    build_views(con)
    print_coverage(con)
    make_snipes_table(con)
    print_snipes(con)
    print_reaction_stats(con)
    print_materiality_filter(con)
    build_preclose_curve(con)
    print_preclose_curve(con)


if __name__ == "__main__":
    main()
