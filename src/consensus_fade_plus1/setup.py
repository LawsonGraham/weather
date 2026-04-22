"""Wallet bootstrap — thin wrapper around Nautilus's setup scripts.

Nautilus ships two helper scripts in the adapter:
  set_allowances.py  — one-time USDC + ConditionalTokens approvals
  create_api_key.py  — derive L2 API credentials

We wrap them so the user runs a single command (`cfp setup`) and we handle
.env rewriting so credentials land in the right variable names.

Also does a balance pre-flight (MATIC/POL + USDC.e) because the #1 new-user
issue is funding the wrong token (native USDC instead of USDC.e).
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[2]
ENV_PATH = REPO_ROOT / ".env"

# Polygon tokens (checksummed)
USDC_E = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"  # Polymarket settlement
USDC_NATIVE = "0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359"  # common funding mistake
DEFAULT_RPC = "https://polygon-bor-rpc.publicnode.com"

# Map legacy (pre-Nautilus) env var names → Nautilus canonical names.
# If .env has the old names we'll read them, and on `cfp setup` we rewrite.
LEGACY_TO_CANONICAL = {
    "PK": "POLYMARKET_PK",
    "CLOB_API_KEY": "POLYMARKET_API_KEY",
    "CLOB_SECRET": "POLYMARKET_API_SECRET",
    "CLOB_PASS_PHRASE": "POLYMARKET_PASSPHRASE",
    "CLOB_FUNDER": "POLYMARKET_FUNDER",
    "POLYGON_RPC": "POLYMARKET_RPC",
}


def _get_pk() -> str | None:
    """Return POLYMARKET_PK, falling back to legacy PK."""
    return os.environ.get("POLYMARKET_PK") or os.environ.get("PK")


def _migrate_env_names() -> int:
    """Rewrite .env to use POLYMARKET_* naming. Returns count of renames."""
    if not ENV_PATH.exists():
        return 0
    text = ENV_PATH.read_text()
    renamed = 0
    out = []
    for line in text.splitlines():
        for old, new in LEGACY_TO_CANONICAL.items():
            if line.startswith(f"{old}=") or line.startswith(f'{old}="'):
                # Check whether the new name is already present elsewhere in file.
                # If so, keep old one as comment.
                if f"{new}=" in text.replace(line, ""):
                    out.append(f"# LEGACY (renamed): {line}")
                else:
                    line = new + line[len(old):]
                renamed += 1
                break
        out.append(line)
    if renamed:
        ENV_PATH.write_text("\n".join(out) + "\n")
    return renamed


def check_setup() -> int:
    """Read-only status check — no transactions, just balances + env state."""
    load_dotenv(ENV_PATH, override=True)
    pk = _get_pk()
    if not pk:
        print("Error: POLYMARKET_PK missing from .env.")
        print("  Copy .env.example to .env and fill POLYMARKET_PK.")
        print("  (If you have an old PK= line from a previous version,")
        print("   rename it to POLYMARKET_PK= — same value works.)")
        return 2

    rpc = os.environ.get("POLYMARKET_RPC") or DEFAULT_RPC
    from eth_account import Account
    address = Account.from_key(pk).address

    from web3 import Web3
    w3 = Web3(Web3.HTTPProvider(rpc, request_kwargs={"timeout": 30}))
    try:
        block = w3.eth.block_number
    except Exception as e:
        print(f"Error: RPC unreachable at {rpc}: {e}")
        return 2
    print(f"Signer:        {address}")
    print(f"RPC:           {rpc} (block {block})")

    pol_bal = w3.eth.get_balance(w3.to_checksum_address(address))
    print(f"POL balance:   {pol_bal / 1e18:.6f} POL (gas)")

    balance_of_abi = [{"constant": True,
                      "inputs": [{"name": "a", "type": "address"}],
                      "name": "balanceOf",
                      "outputs": [{"name": "", "type": "uint256"}],
                      "stateMutability": "view", "type": "function"}]
    usdc_e = w3.eth.contract(address=USDC_E, abi=balance_of_abi)
    usdc_native = w3.eth.contract(address=USDC_NATIVE, abi=balance_of_abi)
    usdc_e_bal = usdc_e.functions.balanceOf(address).call() / 1e6
    usdc_native_bal = usdc_native.functions.balanceOf(address).call() / 1e6

    print(f"USDC.e:        {usdc_e_bal:.4f} (Polymarket settlement token)")
    if usdc_native_bal > 0 and usdc_e_bal < 1:
        print(f"USDC (native): {usdc_native_bal:.4f} — WRONG TOKEN!")
        print("  Swap native USDC → USDC.e on Uniswap:")
        print("  https://app.uniswap.org/#/swap"
              "?inputCurrency=0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359"
              "&outputCurrency=0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
              "&chain=polygon")

    creds = all(os.environ.get(v) for v in
                ("POLYMARKET_API_KEY", "POLYMARKET_API_SECRET", "POLYMARKET_PASSPHRASE"))
    print(f"API creds:     {'present' if creds else 'MISSING'}")

    ready = creds and pol_bal > 5e16 and usdc_e_bal > 0
    print(f"\nStatus: {'READY' if ready else 'NOT READY'}")
    if not ready:
        if pol_bal < 5e16:
            print("  → Fund POL on Polygon for gas")
        if usdc_e_bal <= 0:
            print("  → Fund USDC.e (bridged) on Polygon for collateral")
        if not creds:
            print("  → Run `cfp setup` to derive + persist L2 API creds")
    return 0


def run_setup(force_rederive: bool = False) -> int:
    """Run Nautilus's allowance + cred scripts, then rewrite .env.

    The scripts read POLYGON_PRIVATE_KEY / POLYGON_PUBLIC_KEY env vars; we
    set those from our POLYMARKET_PK before invoking.
    """
    # Migrate old env var names (PK → POLYMARKET_PK etc.) if present
    renamed = _migrate_env_names()
    if renamed:
        print(f"[setup] migrated {renamed} legacy env var name(s) in .env")

    load_dotenv(ENV_PATH, override=True)
    pk = _get_pk()
    if not pk:
        print("Error: POLYMARKET_PK missing from .env.", file=sys.stderr)
        return 2
    from eth_account import Account
    address = Account.from_key(pk).address

    print(f"Signer: {address}")
    print("Running Nautilus allowance + API-key setup...")
    print("(Nautilus scripts read POLYGON_PRIVATE_KEY / POLYGON_PUBLIC_KEY)")

    env = {
        **os.environ,
        "POLYGON_PRIVATE_KEY": pk,
        "POLYGON_PUBLIC_KEY": address,
        "POLYMARKET_PK": pk,
        "POLYMARKET_FUNDER": os.environ.get("POLYMARKET_FUNDER") or address,
    }

    # 1. Allowances — only run if needed
    status_code = check_setup()
    if status_code == 0:
        # Allowances shown present — just ensure creds are derived
        print("\n(allowances appear to be set; skipping set_allowances.py)")
    else:
        print("\n--- Step 1: allowances (6 on-chain txs, ~$1 gas) ---")
        script = _find_script("set_allowances.py")
        if script is None:
            print("Error: couldn't find Nautilus set_allowances.py")
            return 2
        rc = subprocess.run([sys.executable, str(script)], env=env).returncode
        if rc != 0:
            print(f"set_allowances.py failed with code {rc}", file=sys.stderr)
            return rc

    # 2. Derive API creds
    print("\n--- Step 2: derive L2 API credentials ---")
    script = _find_script("create_api_key.py")
    if script is None:
        print("Error: couldn't find Nautilus create_api_key.py")
        return 2
    result = subprocess.run([sys.executable, str(script)],
                          env=env, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"create_api_key.py failed:\n{result.stderr}", file=sys.stderr)
        return result.returncode
    print(result.stdout)

    # Parse out api key / secret / passphrase from script output and persist.
    # Nautilus's create_api_key.py prints:
    #   ApiCreds(api_key='...', api_secret='...', api_passphrase='...')
    import re
    creds: dict[str, str] = {}
    m = re.search(
        r"api_key=['\"]([^'\"]+)['\"].*?"
        r"api_secret=['\"]([^'\"]+)['\"].*?"
        r"api_passphrase=['\"]([^'\"]+)['\"]",
        result.stdout,
    )
    if m:
        creds = {
            "POLYMARKET_API_KEY": m.group(1),
            "POLYMARKET_API_SECRET": m.group(2),
            "POLYMARKET_PASSPHRASE": m.group(3),
        }

    if creds:
        _upsert_env(ENV_PATH, creds)
        print(f"\nSaved {len(creds)} credentials to {ENV_PATH}")

    print("\nSetup complete. Run `cfp setup --check` to verify.")
    return 0


def _find_script(name: str) -> Path | None:
    """Locate a Nautilus adapter script via the installed package."""
    try:
        import nautilus_trader
    except ImportError:
        return None
    # scripts dir lives inside the package
    package_dir = Path(nautilus_trader.__file__).parent
    candidate = package_dir / "adapters" / "polymarket" / "scripts" / name
    return candidate if candidate.exists() else None


def _upsert_env(path: Path, updates: dict[str, str]) -> None:
    """Update or append env vars in a .env file."""
    lines = []
    seen = set()
    if path.exists():
        for raw in path.read_text().splitlines():
            line = raw
            for key, val in updates.items():
                if raw.startswith(f"{key}=") or raw.startswith(f'{key}="'):
                    line = f'{key}="{val}"'
                    seen.add(key)
                    break
            lines.append(line)
    for key, val in updates.items():
        if key not in seen:
            lines.append(f'{key}="{val}"')
    path.write_text("\n".join(lines) + "\n")
