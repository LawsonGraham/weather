"""Redeem winning Polymarket CTF positions on-chain from the EOA.

Polymarket has two market shapes, each with a different redemption contract:
  - neg_risk=True  → `NegRiskAdapter.redeemPositions(conditionId, [yes, no])`
  - neg_risk=False → `ConditionalTokens.redeemPositions(USDC, 0x0, conditionId, [1,2])`

Both are one-tx-per-market. `neg_risk` flag comes from `markets.parquet`
(or the Gamma API field `negRisk`). All daily-temperature bucket markets
the bot trades are neg_risk=True; this module handles both for safety.

Dry-run by default (`broadcast=False`): prints resolution state, balances,
and the expected payout without signing anything. Pass `broadcast=True` to
send the tx. Idempotent: if the outcome token balance is already zero,
nothing is broadcast.
"""
from __future__ import annotations

import os
from pathlib import Path

import duckdb
from dotenv import load_dotenv
from eth_account import Account
from web3 import Web3
from web3.middleware import ExtraDataToPOAMiddleware

REPO_ROOT = Path(__file__).resolve().parents[3]
MARKETS_PATH = REPO_ROOT / "data" / "processed" / "polymarket_weather" / "markets.parquet"
DEFAULT_RPC = "https://polygon-bor-rpc.publicnode.com"

USDC_E = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
CTF = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"
NEG_RISK_ADAPTER = "0xd91E80cF2E7be2e162c6513ceD06f1dD0dA35296"

_CTF_ABI = [
    {"inputs": [{"name": "conditionId", "type": "bytes32"}], "name": "payoutDenominator",
     "outputs": [{"name": "", "type": "uint256"}], "stateMutability": "view", "type": "function"},
    {"inputs": [{"name": "conditionId", "type": "bytes32"}, {"name": "index", "type": "uint256"}],
     "name": "payoutNumerators", "outputs": [{"name": "", "type": "uint256"}],
     "stateMutability": "view", "type": "function"},
    {"inputs": [{"name": "a", "type": "address"}, {"name": "id", "type": "uint256"}],
     "name": "balanceOf", "outputs": [{"name": "", "type": "uint256"}],
     "stateMutability": "view", "type": "function"},
    {"inputs": [{"name": "collateralToken", "type": "address"},
                {"name": "parentCollectionId", "type": "bytes32"},
                {"name": "conditionId", "type": "bytes32"},
                {"name": "indexSets", "type": "uint256[]"}],
     "name": "redeemPositions", "outputs": [], "stateMutability": "nonpayable",
     "type": "function"},
]
_NRA_ABI = [
    {"inputs": [{"name": "_conditionId", "type": "bytes32"},
                {"name": "_amounts", "type": "uint256[]"}],
     "name": "redeemPositions", "outputs": [], "stateMutability": "nonpayable",
     "type": "function"},
]
_USDC_ABI = [
    {"inputs": [{"name": "a", "type": "address"}], "name": "balanceOf",
     "outputs": [{"name": "", "type": "uint256"}], "stateMutability": "view",
     "type": "function"},
]


def _lookup_market(condition_id: str | None, slug: str | None) -> dict:
    """Return {condition_id, neg_risk, yes_token_id, no_token_id, slug, city, bucket}.

    Given either a condition_id (hex string) or slug, look the row up in
    `markets.parquet`. Raises if not found — refuse to redeem markets we
    never tracked, as a guard against typos in the hex id.
    """
    if not MARKETS_PATH.exists():
        raise FileNotFoundError(f"{MARKETS_PATH} missing — run `cfp watch markets` first.")
    where = (f"condition_id = '{condition_id}'" if condition_id
             else f"slug = '{slug}'")
    df = duckdb.sql(f"""
        SELECT condition_id, neg_risk, yes_token_id, no_token_id, slug,
               city, group_item_title
        FROM '{MARKETS_PATH}'
        WHERE {where}
        LIMIT 1
    """).df()
    if df.empty:
        raise ValueError(f"No market found in markets.parquet for {where}. "
                         f"Update the markets parquet (`cfp watch markets`) or double-check the id.")
    r = df.iloc[0]
    return {
        "condition_id": str(r["condition_id"]),
        "neg_risk": bool(r["neg_risk"]),
        "yes_token_id": int(r["yes_token_id"]),
        "no_token_id": int(r["no_token_id"]),
        "slug": str(r["slug"]),
        "city": str(r["city"]),
        "bucket": str(r["group_item_title"]),
    }


def _w3() -> Web3:
    rpc = os.environ.get("POLYMARKET_RPC") or DEFAULT_RPC
    w3 = Web3(Web3.HTTPProvider(rpc, request_kwargs={"timeout": 20}))
    w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
    return w3


def redeem(
    *,
    condition_id: str | None = None,
    slug: str | None = None,
    broadcast: bool = False,
    priority_gwei: int = 30,
) -> int:
    """Redeem the caller's winning position for a resolved Polymarket market.

    Exactly one of `condition_id` / `slug` must be given. Returns 0 on
    success (including no-op idempotent skips), 1 on failure.
    """
    if bool(condition_id) == bool(slug):
        print("error: pass exactly one of --condition-id or --slug")
        return 1
    load_dotenv(REPO_ROOT / ".env")
    pk = os.environ.get("POLYMARKET_PK")
    if not pk:
        print("error: POLYMARKET_PK not set in .env")
        return 1
    acc = Account.from_key(pk)
    eoa = acc.address

    m = _lookup_market(condition_id, slug)
    print(f"market   : {m['city']} {m['bucket']}  ({'neg-risk' if m['neg_risk'] else 'vanilla CTF'})")
    print(f"slug     : {m['slug']}")
    print(f"condition: {m['condition_id']}")
    print(f"eoa      : {eoa}")

    w3 = _w3()
    cond_bytes = bytes.fromhex(m["condition_id"][2:])
    ctf = w3.eth.contract(address=Web3.to_checksum_address(CTF), abi=_CTF_ABI)
    usdc = w3.eth.contract(address=Web3.to_checksum_address(USDC_E), abi=_USDC_ABI)

    denom = ctf.functions.payoutDenominator(cond_bytes).call()
    if denom == 0:
        print("→ condition not resolved yet; nothing to redeem.")
        return 0
    num_yes = ctf.functions.payoutNumerators(cond_bytes, 0).call()
    num_no = ctf.functions.payoutNumerators(cond_bytes, 1).call()
    print(f"resolved : YES={num_yes}/{denom}  NO={num_no}/{denom}  "
          f"(winner={'NO' if num_no > num_yes else 'YES'})")

    yes_bal = ctf.functions.balanceOf(eoa, m["yes_token_id"]).call()
    no_bal = ctf.functions.balanceOf(eoa, m["no_token_id"]).call()
    print(f"balance  : YES={yes_bal/1e6:.6f}  NO={no_bal/1e6:.6f}")
    if yes_bal == 0 and no_bal == 0:
        print("→ 0 balance; nothing to redeem (already redeemed or never held).")
        return 0

    payout_raw = (yes_bal * num_yes + no_bal * num_no) // denom
    print(f"payout   : {payout_raw/1e6:.6f} USDC.e expected")

    if m["neg_risk"]:
        target = Web3.to_checksum_address(NEG_RISK_ADAPTER)
        c = w3.eth.contract(address=target, abi=_NRA_ABI)
        fn = c.functions.redeemPositions(cond_bytes, [yes_bal, no_bal])
    else:
        target = Web3.to_checksum_address(CTF)
        parent = b"\x00" * 32
        c = w3.eth.contract(address=target, abi=_CTF_ABI)
        fn = c.functions.redeemPositions(
            Web3.to_checksum_address(USDC_E), parent, cond_bytes, [1, 2],
        )
    print(f"target   : {target}")

    if not broadcast:
        print("\n[dry-run] re-run with --broadcast to send.")
        return 0

    usdc_before = usdc.functions.balanceOf(eoa).call()
    base = w3.eth.get_block("latest")["baseFeePerGas"]
    priority = w3.to_wei(priority_gwei, "gwei")
    max_fee = base * 2 + priority
    est_gas = fn.estimate_gas({"from": eoa})
    tx = fn.build_transaction({
        "from": eoa, "nonce": w3.eth.get_transaction_count(eoa),
        "chainId": w3.eth.chain_id, "gas": int(est_gas * 1.3),
        "maxFeePerGas": max_fee, "maxPriorityFeePerGas": priority,
    })
    signed = acc.sign_transaction(tx)
    txh = w3.eth.send_raw_transaction(signed.raw_transaction)
    hx = txh.hex()
    print(f"\nBROADCAST: https://polygonscan.com/tx/0x{hx}")
    print("waiting for receipt (≤120s)...")
    r = w3.eth.wait_for_transaction_receipt(txh, timeout=120)
    status = "SUCCESS" if r.status == 1 else "FAIL"
    print(f"status={status}  block={r.blockNumber}  gasUsed={r.gasUsed}  "
          f"cost={(r.gasUsed * r.effectiveGasPrice)/1e18:.6f} POL")
    if r.status != 1:
        return 1
    usdc_after = usdc.functions.balanceOf(eoa).call()
    print(f"USDC.e: {usdc_before/1e6:.6f} → {usdc_after/1e6:.6f}  "
          f"(+{(usdc_after - usdc_before)/1e6:.6f})")
    return 0
