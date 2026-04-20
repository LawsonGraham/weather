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

DEFAULT_POLYGON_RPC = "https://polygon-bor-rpc.publicnode.com"

# USDC.e = "PoS-bridged USDC" — this is the version Polymarket uses.
# NOT the same as native USDC (0x3c499c...), which is a separate Circle-issued
# token. Sending "USDC" from a CEX often gets you native USDC instead — users
# will need to swap to USDC.e before they can trade. See _print_token_warning.
USDC_CONTRACT = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"  # USDC.e
USDC_NATIVE_CONTRACT = "0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359"  # native USDC
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
    rpc_url: str
    rpc_reachable: bool
    pol_balance_wei: int  # native gas token (POL, formerly MATIC)
    usdc_balance_raw: int  # USDC.e (Polymarket's token), 6 decimals
    usdc_native_balance_raw: int  # native USDC (wrong token — warn if present), 6 decimals
    usdc_allowances_ok: dict[str, bool]
    ctf_allowances_ok: dict[str, bool]
    api_creds_present: bool
    api_creds_match: bool | None  # None = not checked

    @property
    def pol_balance(self) -> float:
        return self.pol_balance_wei / 1e18

    @property
    def usdc_balance(self) -> float:
        return self.usdc_balance_raw / 1e6

    @property
    def usdc_native_balance(self) -> float:
        return self.usdc_native_balance_raw / 1e6

    @property
    def has_wrong_token(self) -> bool:
        """True if wallet has native USDC but zero USDC.e — common funding error."""
        return self.usdc_native_balance_raw > 0 and self.usdc_balance_raw == 0


def _rpc_url() -> str:
    return os.environ.get("POLYGON_RPC", DEFAULT_POLYGON_RPC)


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
    rpc = _rpc_url()
    w3 = Web3(Web3.HTTPProvider(rpc, request_kwargs={"timeout": 30}))

    rpc_reachable = False
    pol_balance_wei = 0
    usdc_balance_raw = 0
    usdc_native_balance_raw = 0
    usdc_ok: dict[str, bool] = dict.fromkeys(EXCHANGE_CONTRACTS, False)
    ctf_ok: dict[str, bool] = dict.fromkeys(EXCHANGE_CONTRACTS, False)

    try:
        block = w3.eth.block_number
        rpc_reachable = block > 0
    except Exception as e:
        raise RuntimeError(
            f"RPC at {rpc} is unreachable: {e}\n"
            f"Try a different endpoint by setting POLYGON_RPC in .env, e.g.:\n"
            f'  POLYGON_RPC="https://polygon-bor-rpc.publicnode.com"'
        ) from e

    try:
        pol_balance_wei = int(w3.eth.get_balance(w3.to_checksum_address(address)))
    except Exception as e:
        print(f"[warn] couldn't fetch POL balance: {e}")

    erc20_with_balance = [*ERC20_ABI, {
        "constant": True,
        "inputs": [{"name": "account", "type": "address"}],
        "name": "balanceOf",
        "outputs": [{"name": "", "type": "uint256"}],
        "stateMutability": "view", "type": "function",
    }]
    try:
        usdc = w3.eth.contract(address=w3.to_checksum_address(USDC_CONTRACT),
                               abi=erc20_with_balance)
        usdc_balance_raw = int(usdc.functions.balanceOf(address).call())
        # Also check native USDC — if present, user likely funded wrong token
        usdc_native = w3.eth.contract(address=w3.to_checksum_address(USDC_NATIVE_CONTRACT),
                                      abi=erc20_with_balance)
        usdc_native_balance_raw = int(usdc_native.functions.balanceOf(address).call())
        ctf = w3.eth.contract(address=w3.to_checksum_address(CTF_CONTRACT), abi=ERC1155_ABI)
        for name, addr in EXCHANGE_CONTRACTS.items():
            a = w3.to_checksum_address(addr)
            allow = usdc.functions.allowance(address, a).call()
            usdc_ok[name] = int(allow) >= (MAX_UINT256 >> 1)
            approved = ctf.functions.isApprovedForAll(address, a).call()
            ctf_ok[name] = bool(approved)
    except Exception as e:
        print(f"[warn] couldn't read allowances: {e}")

    api_creds_present = all(
        os.environ.get(k) for k in ("CLOB_API_KEY", "CLOB_SECRET", "CLOB_PASS_PHRASE")
    )

    return SetupStatus(
        address=address,
        rpc_url=rpc,
        rpc_reachable=rpc_reachable,
        pol_balance_wei=pol_balance_wei,
        usdc_balance_raw=usdc_balance_raw,
        usdc_native_balance_raw=usdc_native_balance_raw,
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
    print(f"[setup] RPC endpoint:   {status.rpc_url}")
    print(f"[setup] POL balance:    {status.pol_balance:.6f} POL (gas)")
    print(f"[setup] USDC balance:   {status.usdc_balance:.4f} USDC (collateral)")
    print()

    # Pre-flight: confirm wallet is actually funded before attempting any tx
    needs_tx = not (all(status.usdc_allowances_ok.values())
                    and all(status.ctf_allowances_ok.values()))

    if needs_tx and not skip_tx:
        if status.pol_balance_wei < int(0.05e18):
            raise RuntimeError(
                f"Not enough POL for gas. Wallet {status.address} has "
                f"{status.pol_balance:.6f} POL. Need at least 0.05 POL (~$0.04 at "
                f"current price) to cover 6 approval transactions. "
                f"Send POL (formerly MATIC) on Polygon mainnet to this address."
            )
        if status.usdc_balance_raw == 0:
            print("[setup] WARNING: 0 USDC balance. Allowances will still be set, "
                  "but you can't place orders without USDC collateral.")
            print("[setup] USDC must be sent on Polygon mainnet (chain_id 137), not "
                  "Ethereum or another L2.")

    # 1) USDC + ConditionalTokens allowances
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

    w3 = Web3(Web3.HTTPProvider(_rpc_url(), request_kwargs={"timeout": 30}))
    w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
    acct = Account.from_key(pk)

    usdc = w3.eth.contract(address=w3.to_checksum_address(USDC_CONTRACT), abi=ERC20_ABI)
    ctf = w3.eth.contract(address=w3.to_checksum_address(CTF_CONTRACT), abi=ERC1155_ABI)

    def _send(tx_func, *, label: str):
        nonce = w3.eth.get_transaction_count(acct.address)
        # Grab current base fee from chain and add a 2 gwei priority tip.
        try:
            block = w3.eth.get_block("latest")
            base_fee = int(block.get("baseFeePerGas", w3.to_wei(30, "gwei")))
        except Exception:
            base_fee = w3.to_wei(30, "gwei")
        priority = w3.to_wei(30, "gwei")  # Polygon requires ≥30 gwei priority
        max_fee = base_fee * 2 + priority
        tx = tx_func.build_transaction({
            "from": acct.address,
            "nonce": nonce,
            "chainId": 137,
            "gas": 200_000,
            "maxFeePerGas": max_fee,
            "maxPriorityFeePerGas": priority,
        })
        try:
            signed = acct.sign_transaction(tx)
            tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
        except Exception as e:
            raise RuntimeError(
                f"[setup] {label}: failed to submit tx: {e}"
            ) from e
        print(f"[setup] {label}: sent tx 0x{tx_hash.hex()}")
        try:
            receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
        except Exception as e:
            raise RuntimeError(
                f"[setup] {label}: tx 0x{tx_hash.hex()} not confirmed within 120s: {e}. "
                f"Check https://polygonscan.com/tx/0x{tx_hash.hex()}"
            ) from e
        if receipt["status"] != 1:
            raise RuntimeError(
                f"[setup] {label}: tx 0x{tx_hash.hex()} reverted on-chain. "
                f"See https://polygonscan.com/tx/0x{tx_hash.hex()}"
            )
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
