import marimo

__generated_with = "0.23.1"
app = marimo.App(width="medium")


@app.cell
def _():
    import marimo as mo

    return (mo,)


@app.cell
def _(mo):
    mo.md(
        r"""
        # Prediction Market Dataset — First Look

        The 36 GiB Kalshi + Polymarket dataset from
        [jon-becker/prediction-market-analysis](https://github.com/jon-becker/prediction-market-analysis)
        is already downloaded to `data/raw/prediction_market_analysis/`.

        This notebook:

        1. Lists the Parquet files under `kalshi/` and `polymarket/`
        2. Describes the schema of the first file per venue
        3. Shows sample rows

        **Exploratory only.** No mutations. The real Phase-0 weather-subset
        filter and calibration analysis belongs in a separate notebook
        (`calib_weather_markets.py` or similar).
        """
    )
    return


@app.cell
def _():
    import duckdb
    import polars as pl

    return duckdb, pl


@app.cell
def _(duckdb):
    con = duckdb.connect()
    return (con,)


@app.cell
def _(con):
    files = con.sql(
        """
        SELECT file
        FROM glob('data/raw/prediction_market_analysis/**/*.parquet')
        ORDER BY file
        """
    ).pl()
    return (files,)


@app.cell
def _(files, mo):
    mo.md(f"**{len(files)} parquet files** under `data/raw/prediction_market_analysis/`")
    return


@app.cell
def _(files, mo):
    mo.ui.table(files.head(100), page_size=25)
    return


@app.cell
def _(con, files, mo):
    kalshi_files = files.filter(files["file"].str.contains("kalshi"))
    if len(kalshi_files) == 0:
        kalshi_display = mo.md("_No Kalshi parquet files found._")
    else:
        first_kalshi = kalshi_files["file"][0]
        kalshi_schema = con.sql(f"DESCRIBE SELECT * FROM '{first_kalshi}'").pl()
        kalshi_display = mo.vstack(
            [
                mo.md(f"### Kalshi — schema of first file\n\n`{first_kalshi}`"),
                mo.ui.table(kalshi_schema, page_size=50),
            ]
        )
    kalshi_display
    return (kalshi_display,)


@app.cell
def _(con, files, mo):
    poly_files = files.filter(files["file"].str.contains("polymarket"))
    if len(poly_files) == 0:
        poly_display = mo.md("_No Polymarket parquet files found._")
    else:
        first_poly = poly_files["file"][0]
        poly_schema = con.sql(f"DESCRIBE SELECT * FROM '{first_poly}'").pl()
        poly_display = mo.vstack(
            [
                mo.md(f"### Polymarket — schema of first file\n\n`{first_poly}`"),
                mo.ui.table(poly_schema, page_size=50),
            ]
        )
    poly_display
    return (poly_display,)


if __name__ == "__main__":
    app.run()
