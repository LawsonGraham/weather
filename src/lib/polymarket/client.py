"""Polymarket CLOB client wrapper with env-based config."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

CLOB_HOST = "https://clob.polymarket.com"
POLYGON_CHAIN_ID = 137

REPO_ROOT = Path(__file__).resolve().parents[3]


@dataclass
class PolymarketClient:
    """Wraps py-clob-client's ClobClient with our env-derived creds.

    Attributes:
        clob: the underlying py-clob-client instance
        address: signer address (0x...)
    """
    clob: object  # py_clob_client.client.ClobClient — avoid importing at module load
    address: str

    def get_order_book(self, token_id: str):
        return self.clob.get_order_book(token_id)  # type: ignore[attr-defined]

    def get_tick_size(self, token_id: str) -> str:
        return self.clob.get_tick_size(token_id)  # type: ignore[attr-defined]

    def get_neg_risk(self, token_id: str) -> bool:
        return self.clob.get_neg_risk(token_id)  # type: ignore[attr-defined]


def load_client_from_env(dotenv_path: Path | str | None = None) -> PolymarketClient:
    """Construct a PolymarketClient from environment variables.

    Expects in .env:
        PK               — Polygon EOA private key (0x...)
        CLOB_API_KEY     — derived via setup
        CLOB_SECRET      — derived via setup
        CLOB_PASS_PHRASE — derived via setup

    Optional:
        CLOB_FUNDER  — only if using proxy/email wallet (signature_type != 0)
    """
    if dotenv_path is None:
        dotenv_path = REPO_ROOT / ".env"
    load_dotenv(dotenv_path, override=False)

    pk = _require("PK")
    api_key = _require("CLOB_API_KEY")
    api_secret = _require("CLOB_SECRET")
    api_passphrase = _require("CLOB_PASS_PHRASE")
    funder = os.environ.get("CLOB_FUNDER")
    sig_type = int(os.environ.get("CLOB_SIG_TYPE", "0"))

    # Lazy import — keeps the module importable when py-clob-client
    # is not yet installed (e.g. during initial setup checks).
    from py_clob_client.client import ClobClient
    from py_clob_client.clob_types import ApiCreds

    kwargs: dict = dict(
        host=CLOB_HOST,
        key=pk,
        chain_id=POLYGON_CHAIN_ID,
        creds=ApiCreds(
            api_key=api_key,
            api_secret=api_secret,
            api_passphrase=api_passphrase,
        ),
        signature_type=sig_type,
    )
    if funder and sig_type != 0:
        kwargs["funder"] = funder

    clob = ClobClient(**kwargs)
    address = _address_from_pk(pk)
    return PolymarketClient(clob=clob, address=address)


def _require(key: str) -> str:
    val = os.environ.get(key)
    if not val:
        raise RuntimeError(
            f"Missing env var: {key}. Populate .env — see .env.example. "
            f"If this is your first run, execute `uv run cfp setup`."
        )
    return val


def _address_from_pk(pk: str) -> str:
    from eth_account import Account
    acct = Account.from_key(pk)
    return acct.address
