"""HTTP API + demo UI for the Solana deposit -> Gas lane.

Endpoints mirror production (`/api/points/solana/*` there, `/api/solana/*` here).
Login is a deliberate stand-in: production authenticates with a signed wallet
message; this module only needs *a* login identity (a 0x address) to attribute
deposits to, so `POST /api/login` issues a bearer token for it.

Run:  uvicorn maneki_solana.server:app --port 4190
"""
from __future__ import annotations

import asyncio
import datetime as dt
import logging
import os
import re
import secrets
import time
import uuid
from pathlib import Path
from typing import Any, Dict

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse

from . import config, deposits, ledger, pricing, solana_chain

log = logging.getLogger("maneki.server")
app = FastAPI(title="Maneki AI · Solana deposit + Gas")
WEB = Path(__file__).resolve().parent.parent / "web"

_ADDR_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")
_sessions: Dict[str, str] = {}                       # bearer token -> login address
_bind_challenges: Dict[str, tuple] = {}              # f"{login}|{pubkey}" -> (message, expires_at)
_BIND_TTL_S = 300.0
_FEE_BUFFER_SOL = 0.005


@app.exception_handler(Exception)
async def _redact(request: Request, exc: Exception):
    """User-visible errors never carry internals; the log id ties to the log."""
    lid = uuid.uuid4().hex[:10]
    log.error("unhandled [%s] %s %s: %r", lid, request.method, request.url.path, exc)
    return JSONResponse({"detail": f"Something went wrong. Reference: {lid}"}, status_code=500)


def require_login(authorization: str = Header(default="")) -> str:
    tok = authorization.removeprefix("Bearer ").strip()
    addr = _sessions.get(tok)
    if not addr:
        raise HTTPException(401, "Please log in first.")
    return addr


def _cfg_or_503() -> Dict[str, Any]:
    cfg = config.chain_cfg()
    if not cfg["enabled"]:
        raise HTTPException(503, "Solana deposits are not configured on this instance.")
    return cfg


# -------------------------------------------------------------- public ------

@app.get("/")
def index():
    return FileResponse(WEB / "index.html")


@app.get("/api/config")
def api_config() -> Dict[str, Any]:
    cfg = config.chain_cfg()
    chain = None
    if cfg["enabled"]:
        spl = solana_chain.effective_mints(cfg["network"], cfg["spl"])
        chain = {"id": "SOLANA", "name": "Solana", "kind": "solana", "treasury": cfg["treasury"],
                 "network": cfg["network"], "solana": solana_chain.ui_params(cfg["network"]),
                 "tokens": [{"symbol": "SOL", "decimals": 9, "price_usd": pricing.token_price_usd("SOL") or 0.0}] +
                           [{"symbol": s, "decimals": t["decimals"], "price_usd": pricing.token_price_usd(s) or 0.0}
                            for s, t in spl.items()]}
    return {"enabled": cfg["enabled"], "chain": chain, "pricing": pricing.public_pricing()}


@app.post("/api/login")
async def api_login(request: Request) -> Dict[str, Any]:
    body = await request.json()
    addr = str(body.get("address") or "").strip().lower()
    if not _ADDR_RE.match(addr):
        raise HTTPException(400, "address must be a 0x… EVM address")
    ledger.ensure_user(addr)
    tok = secrets.token_urlsafe(24)
    _sessions[tok] = addr
    return {"token": tok, "address": addr}


@app.get("/api/gas")
def api_gas(address: str = Depends(require_login)) -> Dict[str, Any]:
    return {"address": address, "balance": ledger.balance(address), "history": ledger.history(address, 50)}


# -------------------------------------------------------------- solana ------

@app.post("/api/solana/bind-challenge")
async def bind_challenge(request: Request, address: str = Depends(require_login)) -> Dict[str, Any]:
    pubkey = str((await request.json()).get("pubkey") or "").strip()
    if not solana_chain.is_pubkey(pubkey):
        raise HTTPException(400, "pubkey must be a base58 Solana address")
    _cfg_or_503()
    if deposits.is_bound(pubkey, address):
        return {"bound": True, "pubkey": pubkey}
    iso = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    message = (f"ManekiAI · bind Solana wallet {pubkey} to {address} · nonce {secrets.token_hex(8)} · {iso}\n"
               f"Signing proves you control this wallet so its deposits credit your account. "
               f"No transaction, no fee.")
    now = time.time()
    for k in [k for k, v in _bind_challenges.items() if v[1] < now]:
        _bind_challenges.pop(k, None)
    _bind_challenges[f"{address}|{pubkey}"] = (message, now + _BIND_TTL_S)
    return {"bound": False, "pubkey": pubkey, "message": message, "expires_in": int(_BIND_TTL_S)}


@app.post("/api/solana/bind")
async def bind(request: Request, address: str = Depends(require_login)) -> Dict[str, Any]:
    body = await request.json()
    pubkey = str(body.get("pubkey") or "").strip()
    signature = str(body.get("signature") or "").strip()          # base64 of the 64-byte ed25519 signature
    if not solana_chain.is_pubkey(pubkey) or not signature:
        raise HTTPException(400, "pubkey and signature are required")
    ent = _bind_challenges.get(f"{address}|{pubkey}")
    if not ent or ent[1] < time.time():
        raise HTTPException(400, "Bind challenge expired — start again.")
    ok = await asyncio.to_thread(solana_chain.verify_bind_signature, pubkey, ent[0], signature)
    if not ok:
        raise HTTPException(400, "Signature does not match this wallet.")
    _bind_challenges.pop(f"{address}|{pubkey}", None)             # one-time
    deposits.remember_sender(pubkey, address)
    return {"bound": True, "pubkey": pubkey}


@app.post("/api/solana/build")
async def build(request: Request, address: str = Depends(require_login)) -> Dict[str, Any]:
    """Unsigned SOL / SPL transfer to the treasury (+ attribution memo).
    Body: {sender_pubkey, token, amount | gas}."""
    body = await request.json()
    pubkey = str(body.get("sender_pubkey") or "").strip()
    token = str(body.get("token") or "SOL").strip().upper()
    try:
        gas = float(body.get("gas") or 0)
        amount = float(body.get("amount") or 0)
    except (TypeError, ValueError):
        raise HTTPException(400, "invalid deposit request")
    if not solana_chain.is_pubkey(pubkey):
        raise HTTPException(400, "sender_pubkey must be a base58 Solana address")
    if gas <= 0 and amount <= 0:
        raise HTTPException(400, "gas or amount is required")
    cfg = _cfg_or_503()
    if not deposits.is_bound(pubkey, address):
        raise HTTPException(409, "Bind this Solana wallet first.")
    token_cfg = None
    if token != "SOL":
        tc = solana_chain.effective_mints(cfg["network"], cfg["spl"]).get(token)
        if not tc:
            raise HTTPException(400, f"{token} is not enabled on this chain.")
        token_cfg = {**tc, "symbol": token}
    price = await asyncio.to_thread(pricing.token_price_usd, token)
    if not price:
        raise HTTPException(503, f"{token} price feed unavailable — please retry shortly.")
    if amount <= 0:
        amount = pricing.amount_for_credits(gas, price)
    amount = float(f"{amount:.9f}") if token == "SOL" else float(f"{amount:.6f}")
    if amount <= 0:
        raise HTTPException(400, "amount too small")
    # Balance pre-flight is best-effort: unreadable != zero, so a lookup failure never blocks.
    try:
        sol_bal = await asyncio.to_thread(solana_chain.get_sol_balance, cfg["network"], pubkey)
    except Exception:
        sol_bal = None
    if sol_bal is not None:
        need = amount + _FEE_BUFFER_SOL if token == "SOL" else _FEE_BUFFER_SOL
        if sol_bal < need:
            raise HTTPException(400, f"Not enough SOL in your wallet: {sol_bal:.4f} available, ≈{need:.4f} needed incl. fees.")
    if token_cfg:
        try:
            tok_bal, _ = await asyncio.to_thread(solana_chain.get_token_balance, cfg["network"], pubkey, token_cfg["mint"])
        except Exception:
            tok_bal = None
        if tok_bal is not None and tok_bal + 1e-9 < amount:
            raise HTTPException(400, f"Not enough {token} in your wallet: {tok_bal:.4f} available, {amount:.4f} needed.")
    memo = f"{solana_chain.MEMO_PREFIX}{address}"
    try:
        unsigned = await asyncio.to_thread(solana_chain.deposit_build, cfg["network"], pubkey,
                                           cfg["treasury"], amount, token_cfg, memo)
    except solana_chain.ChainError as e:
        log.error("deposit_build: %s", e)
        if "unavailable" in str(e):
            raise HTTPException(503, "Deposit flow is not available on this instance.")
        raise HTTPException(502, "Could not prepare the deposit, please retry.")
    return {**unsigned, "amount": amount, "treasury": cfg["treasury"], "price_usd": price,
            "chain": "SOLANA", "token": token}


@app.post("/api/solana/confirm")
async def confirm(request: Request, address: str = Depends(require_login)) -> Dict[str, Any]:
    sig = str((await request.json()).get("signature") or "").strip()
    try:
        raw = solana_chain.b58decode(sig)
    except ValueError:
        raw = b""
    if len(raw) != 64:
        raise HTTPException(400, "signature must be a base58 Solana transaction signature")
    _cfg_or_503()
    res = await asyncio.to_thread(deposits.credit_signature, address, sig)
    return {"signature": sig, "balance": ledger.balance(address),
            "history": ledger.history(address, 50), **res}


@app.post("/api/gas/scan")
async def scan(address: str = Depends(require_login)) -> Dict[str, Any]:
    """Refresh: credit only deposits THIS login sent (bound wallet or memo)."""
    res = await asyncio.to_thread(deposits.scan_for_user, address)
    return {**res, "balance": ledger.balance(address), "history": ledger.history(address, 50)}


# ------------------------------------------------------- gas is burned -----

@app.post("/api/demo/burn")
async def demo_burn(request: Request, address: str = Depends(require_login)) -> Dict[str, Any]:
    """Demo of the consumption side: an agent run charges Gas through the same
    ledger. In production the agent runtime calls `ledger.charge()` after each
    completed decision cycle; that runtime is not part of this repository."""
    tier = str((await request.json()).get("tier") or "standard")
    cost = pricing.BURN_RATES.get(tier)
    if cost is None:
        raise HTTPException(400, f"tier must be one of {sorted(pricing.BURN_RATES)}")
    if not ledger.try_debit(address, cost, kind="spend", note=f"demo agent cycle · {tier} tier",
                            agent_id="demo-agent"):
        raise HTTPException(402, "Out of Gas — top up with Solana to keep the agent running.")
    return {"spent": cost, "tier": tier, "balance": ledger.balance(address)}


# -------------------------------------------------------------- operator ----

def _require_admin(x_admin_token: str = Header(default="")) -> None:
    want = os.environ.get("ADMIN_TOKEN", "")
    if not want or not secrets.compare_digest(x_admin_token, want):
        raise HTTPException(401, "operator token required")


@app.get("/admin/unattributed")
def admin_unattributed(_: None = Depends(_require_admin)) -> Dict[str, Any]:
    return {"rows": deposits.list_unattributed()}


@app.post("/admin/attribute")
async def admin_attribute(request: Request, _: None = Depends(_require_admin)) -> Dict[str, Any]:
    body = await request.json()
    try:
        return await asyncio.to_thread(deposits.attribute_unattributed,
                                       str(body.get("txhash") or ""), str(body.get("address") or ""))
    except ValueError as e:
        raise HTTPException(400, str(e))
