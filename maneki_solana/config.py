"""Environment-driven configuration. Everything has a safe default; with no
treasury configured the Solana lane is simply switched off."""
from __future__ import annotations

import os
from typing import Dict

from . import solana_chain

# Canonical mainnet mints (Circle / Tether official), 6 decimals each.
SPL_TOKENS: Dict[str, Dict] = {
    "USDC": {"mint": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v", "decimals": 6, "display": "USDC"},
    "USDT": {"mint": "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB", "decimals": 6, "display": "USDT"},
}


def treasury() -> str:
    """Public deposit address (base58, case-sensitive — never lower-cased)."""
    return os.environ.get("SOLANA_TREASURY_ADDRESS", "").strip()


def network() -> str:
    """`mainnet` (default) or `testnet` (= Solana devnet)."""
    v = os.environ.get("SOLANA_NETWORK", "mainnet").strip().lower()
    return "testnet" if v in ("devnet", "testnet") else "mainnet"


def enabled() -> bool:
    return solana_chain.is_pubkey(treasury())


def db_path() -> str:
    return os.environ.get("MANEKI_DB", "data/maneki_solana.db")


def chain_cfg() -> Dict:
    return {"id": "SOLANA", "name": "Solana", "token": "SOL", "treasury": treasury(),
            "network": network(), "provider": "solana", "enabled": enabled(),
            "spl": dict(SPL_TOKENS)}
