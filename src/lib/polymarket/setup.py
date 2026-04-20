"""One-time wallet setup: USDC + ConditionalTokens allowances + API creds derive.

Usage:
    uv run cfp setup           # run the full flow (idempotent)
    uv run cfp setup --check   # read-only status check, no tx submitted

The setup is idempotent: if allowances are already set, web3 calls are
skipped. If creds already exist in .env, derivation is re-run and
verified (should return same values — deterministic per wallet).
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

POLYGON_RPC = "https://polygon-rpc.com"

USDC_CONTRACT = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
CTF_CONTRACT = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"

EXCHANGE_CONTRACTS = {
    "ctf_exchange": "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E",
    "neg_risk_ctf_exchange": "0xC5d563A36AE78145C45a50134d48A1215220f80a",
    "neg_risk_adapter": "0xd91E80cF2E7be2e162c6513ceD06f1dD0dA35296",
}

# minimal ABIs
ERC20_ABI = [
    {"constant": True, "inputs": [{"name": "owner", "type": "address"},
                                  {"name": "spender", "type": "address"}],
     "name": "allowance", "outputs": [{"name": "", "type": "uint256"}],
     "stateMutability": "view", "type": "function"},
    {"constant": False, "inputs": [{"name": "spender", "type": "address"},
                                   {"name": "amount", "type": "uint256"}],
     "name": "approve", "outputs": [{"name": "", "type": "bool"}],
     "stateMutability": "nonpayable", "type": "function"},
]

ERC1155_ABI = [
    {"constant": True, "inputs": [{"name": "owner", "type": "address"},
                                  {"name": "operator", "type": "address"}],
     "name": "isApprovedForAll", "outputs": [{"name": "", "type": "bool"}],
     "stateMutability": "view", "type": "function"},
    {"constant": False, "inputs": [{"name": "operator", "type": "address"},
                                   {"name": "approved", "type": "bool"}],
     "name": "setApprovalForAll", "outputs": [],
     "stateMutability": "nonpayable", "type": "function"},
]

MAX_UINT256 = (1 << 256) - 1
REPO_ROOT = Path(__file__).resolve().parents[3]


@dataclass
class SetupStatus:
    address: str
    usdc_allowances_ok: dict[str, bool]
    ctf_allowances_ok: dict[str, bool]
    api_creds_present: bool
    api_creds_match: bool | None  # None = not checked


def check_setup(dotenv_path: Path | str | None = None) -> SetupStatus:
    """Read-only status check — does NOT submit any transactions."""
    if dotenv_path is None:
        dotenv_path = REPO_ROOT / ".env"
    load_dotenv(dotenv_path, override=False)

    pk = os.environ.get("PK")
    if not pk:
        raise RuntimeError(
            "PK missing from .env. Copy .env.example to .env and fill in PK first."
        )
    from eth_account import Account
    address = Account.from_key(pk).address

    from web3 import Web3
    w3 = Web3(Web3.HTTPProvider(POLYGON_RPC))

    usdc = w3.eth.contract(address=w3.to_checksum_address(USDC_CONTRACT), abi=ERC20_ABI)
    ctf = w3.eth.contract(address=w3.to_checksum_address(CTF_CONTRACT), abi=ERC1155_ABI)

    usdc_ok: dict[str, bool] = {}
    ctf_ok: dict[str, bool] = {}
    for name, addr in EXCHANGE_CONTRACTS.items():
        a = w3.to_checksum_address(addr)
        allow = usdc.functions.allowance(address, a).call()
        usdc_ok[name] = int(allow) >= (MAX_UINT256 >> 1)  # at least 2^255
        approved = ctf.functions.isApprovedForAll(address, a).call()
        ctf_ok[name] = bool(approved)

    api_creds_present = all(
        os.environ.get(k) for k in ("CLOB_API_KEY", "CLOB_SECRET", "CLOB_PASS_PHRASE")
    )

    return SetupStatus(
        address=address,
        usdc_allowances_ok=usdc_ok,
        ctf_allowances_ok=ctf_ok,
        api_creds_present=api_creds_present,
        api_creds_match=None,
    )


def run_setup(
    *,
    dotenv_path: Path | str | None = None,
    skip_tx: bool = False,
    force_rederive: bool = False,
) -> SetupStatus:
    """Run full one-time setup: allowances + creds derive. Idempotent.

    Arguments:
        dotenv_path: path to .env (default: repo_root/.env)
        skip_tx: if True, skip allowance approvals (for credentials-only run)
        force_rederive: re-derive L2 creds even if .env already has them
    """
    if dotenv_path is None:
        dotenv_path = REPO_ROOT / ".env"
    dotenv_path = Path(dotenv_path)
    load_dotenv(dotenv_path, override=False)

    pk = os.environ.get("PK")
    if not pk:
        raise RuntimeError(
            f"PK missing from {dotenv_path}. Copy .env.example to .env first."
        )

    status = check_setup(dotenv_path)
    print(f"[setup] signer address: {status.address}")

    # 1) USDC allowances
    if not skip_tx:
        _ensure_allowances(pk, status)

    # 2) Derive L2 API creds
    if (not status.api_creds_present) or force_rederive:
        _derive_and_persist_creds(pk, dotenv_path)

    return check_setup(dotenv_path)


def _ensure_allowances(pk: str, status: SetupStatus) -> None:
    from eth_account import Account
    from web3 import Web3
    from web3.middleware import ExtraDataToPOAMiddleware

    w3 = Web3(Web3.HTTPProvider(POLYGON_RPC))
    w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
    acct = Account.from_key(pk)

    usdc = w3.eth.contract(address=w3.to_checksum_address(USDC_CONTRACT), abi=ERC20_ABI)
    ctf = w3.eth.contract(address=w3.to_checksum_address(CTF_CONTRACT), abi=ERC1155_ABI)

    def _send(tx_func, *, label: str):
        nonce = w3.eth.get_transaction_count(acct.address)
        tx = tx_func.build_transaction({
            "from": acct.address,
            "nonce": nonce,
            "chainId": 137,
            "gas": 200_000,
            "maxFeePerGas": w3.to_wei(100, "gwei"),
            "maxPriorityFeePerGas": w3.to_wei(30, "gwei"),
        })
        signed = acct.sign_transaction(tx)
        tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
        print(f"[setup] {label}: sent tx {tx_hash.hex()}")
        receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
        if receipt["status"] != 1:
            raise RuntimeError(f"[setup] {label}: tx {tx_hash.hex()} reverted")
        print(f"[setup] {label}: confirmed in block {receipt['blockNumber']}")

    for name, addr in EXCHANGE_CONTRACTS.items():
        spender = Web3.to_checksum_address(addr)
        if not status.usdc_allowances_ok.get(name):
            print(f"[setup] approving USDC for {name} ({addr})...")
            _send(usdc.functions.approve(spender, MAX_UINT256),
                  label=f"USDC approve → {name}")
        else:
            print(f"[setup] USDC already approved for {name}")

        if not status.ctf_allowances_ok.get(name):
            print(f"[setup] approving Conditional Tokens for {name} ({addr})...")
            _send(ctf.functions.setApprovalForAll(spender, True),
                  label=f"CTF approve → {name}")
        else:
            print(f"[setup] CTF already approved for {name}")


def _derive_and_persist_creds(pk: str, dotenv_path: Path) -> None:
    from py_clob_client.client import ClobClient
    print("[setup] deriving L2 API credentials (one-time, deterministic)...")
    bootstrap = ClobClient("https://clob.polymarket.com", key=pk, chain_id=137)
    creds = bootstrap.create_or_derive_api_creds()

    _upsert_env(dotenv_path, {
        "CLOB_API_KEY": creds.api_key,
        "CLOB_SECRET": creds.api_secret,
        "CLOB_PASS_PHRASE": creds.api_passphrase,
    })
    print(f"[setup] wrote CLOB_API_KEY / CLOB_SECRET / CLOB_PASS_PHRASE to {dotenv_path}")
    print("[setup] these are deterministic — re-running setup produces identical creds")


def _upsert_env(path: Path, updates: dict[str, str]) -> None:
    """Append or update env vars in a .env file without clobbering other keys."""
    lines: list[str] = []
    keys_seen: set[str] = set()
    if path.exists():
        for raw in path.read_text().splitlines():
            line = raw
            for key, val in updates.items():
                if raw.startswith(f"{key}="):
                    line = f'{key}="{val}"'
                    keys_seen.add(key)
                    break
            lines.append(line)
    for key, val in updates.items():
        if key not in keys_seen:
            lines.append(f'{key}="{val}"')
    path.write_text("\n".join(lines) + "\n")
