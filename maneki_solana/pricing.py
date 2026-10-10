"""Fuel pricing.

Anchor: 1 USD = 1,000 Fuel. A deposit converts as

    fuel = floor(amount * price_usd * 1.01 * 1000)

(1% top-up bonus; whole numbers only — remainders are dropped, never rounded
up). Stablecoins are credited at par. If no trustworthy SOL/USD price can be
read, the deposit stays PENDING (not credited, not marked seen) and is retried
on the next scan — Fuel is never minted from a guessed price.

Burn rates (what a unit of agent work costs, by model tier) are a plain table
here; the agent runtime that calls `ledger.charge()` is not part of this repo.
"""
from __future__ import annotations

import math
import os
import threading
import time
from typing import Dict, Optional

import httpx

FUEL_PER_USD = 1000.0
DEPOSIT_BONUS_MULT = 1.01

STABLE_USD = {"USDC": 1.0, "USDT": 1.0}
SOL_WINDOW = (5.0, 5000.0)             # sanity window: outside it the feed is "broken"
_TTL_S = 60.0                          # fresh cache
_STALE_MAX_S = 3600.0                  # last-good fallback horizon

# Fuel burned per agent decision cycle, by model tier (public price card).
BURN_RATES: Dict[str, float] = {"chat": 8.0, "standard": 18.0, "gpt": 68.0, "claude": 88.0}

_lock = threading.Lock()
_cache: Dict[str, float] = {"price": 0.0, "at": 0.0}


def _from_hyperliquid() -> Optional[float]:
    r = httpx.post("https://api.hyperliquid.xyz/info", json={"type": "allMids"}, timeout=6.0)
    r.raise_for_status()
    return float(r.json()["SOL"])


def _from_coinbase() -> Optional[float]:
    r = httpx.get("https://api.coinbase.com/v2/prices/SOL-USD/spot", timeout=6.0)
    r.raise_for_status()
    return float(r.json()["data"]["amount"])


_SOURCES = (_from_hyperliquid, _from_coinbase)


def token_price_usd(symbol: str) -> Optional[float]:
    symbol = (symbol or "").strip().upper()
    if symbol in STABLE_USD:
        return STABLE_USD[symbol]
    if symbol != "SOL":
        return None
    override = os.environ.get("SOL_USD_OVERRIDE", "").strip()   # demos / tests
    if override:
        return float(override)
    now = time.time()
    with _lock:
        cached, at = _cache["price"], _cache["at"]
    if cached and now - at < _TTL_S:
        return cached
    lo, hi = SOL_WINDOW
    for src in _SOURCES:
        try:
            p = src()
            if p and lo <= float(p) <= hi:
                with _lock:
                    _cache["price"], _cache["at"] = float(p), now
                return float(p)
        except Exception:
            continue
    if cached and now - at < _STALE_MAX_S:
        return cached
    return None


def credits_for_token(amount: float, price_usd: float) -> float:
    return float(math.floor(float(amount) * float(price_usd) * DEPOSIT_BONUS_MULT * FUEL_PER_USD))


def amount_for_credits(credits: float, price_usd: float) -> float:
    return float(credits) / (float(price_usd) * DEPOSIT_BONUS_MULT * FUEL_PER_USD)


def public_pricing() -> Dict:
    return {"fuel_per_usd": FUEL_PER_USD, "deposit_bonus_mult": DEPOSIT_BONUS_MULT,
            "burn_rates": dict(BURN_RATES), "sol_usd": token_price_usd("SOL") or 0.0}
