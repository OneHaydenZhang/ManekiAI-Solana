"""Deposit pipeline — turns finalized Solana transfers into Fuel.

Gates every deposit must pass, in order (identical for all three entry points:
`credit_signature`, `scan_for_user`, `scan_once`):

  1. mine?       the sender is attributable to this login (proven bind, or memo)
  2. unseen?     the signature is not already in the ledger
  3. priced?     a trustworthy USD price exists (else PENDING, retried later)
  4. finalized?  meta.err == null and commitment == finalized

We only ever read incoming transfers to the treasury — never a balance — so a
refresh can never hand out a lump sum. Unattributable money is parked in
`deposit_unattributed` for an operator; it is never guessed and never deleted.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, List

from . import config, ledger, pricing, solana_chain

log = logging.getLogger("maneki.deposits")

UNATTRIBUTED_MIN_USD = 0.01     # below this a parked deposit is dust, not a claim


# ------------------------------------------------------------ attribution ---

def sender_key(pubkey: str) -> str:
    """Namespaced and case-SENSITIVE (base58)."""
    return f"sol:{pubkey}"


def remember_sender(pubkey: str, address: str) -> None:
    """Record `pubkey` -> login. Only ever called after `verify_bind_signature`."""
    ledger.execute(
        "INSERT INTO deposit_senders(sender_key,address,created_at) VALUES(?,?,?) "
        "ON CONFLICT(sender_key) DO UPDATE SET address=excluded.address",
        (sender_key(pubkey), (address or "").lower(), time.time()))


def is_bound(pubkey: str, address: str) -> bool:
    row = ledger.query_one("SELECT address FROM deposit_senders WHERE sender_key=?", (sender_key(pubkey),))
    return bool(row and row["address"].lower() == (address or "").lower())


def _bound_owner(d: Dict[str, Any]) -> str:
    for k in d.get("sender_keys") or []:
        row = ledger.query_one("SELECT address FROM deposit_senders WHERE sender_key=?", (k,))
        if row:
            return row["address"]
    return ""


def belongs_to(d: Dict[str, Any], user: str) -> bool:
    """Attribute by SENDER, never by balance: a proven bind, or a memo naming
    this exact login ("maneki:<0x>" — the sender chose to credit that login;
    nobody else can lose from it)."""
    u = (user or "").lower()
    if not u:
        return False
    if _bound_owner(d).lower() == u:
        return True
    return (d.get("memo_login") or "").lower() == u


# --------------------------------------------------------------- credit -----

def _conversion(token: str) -> Dict[str, Any]:
    price = pricing.token_price_usd(token)
    return {"mode": "usd", "price": price} if price else {"mode": "pending"}


def credit_deposit(address: str, amt: float, txh: str, price: float, token: str,
                   block: int | None = None, ts: int | None = None) -> float:
    """Credit one deposit. Returns the Fuel credited (0.0 for a duplicate or dust)."""
    fuel = pricing.credits_for_token(amt, price)
    usd = amt * price
    meta: Dict[str, Any] = {"mode": "usd"}
    if block is not None:
        meta["slot"] = int(block)
    if ts is not None:
        meta["block_ts"] = int(ts)
    note = f"deposit {amt:.6f} {token} @ ${price:.2f} -> {fuel:.0f} Fuel"
    try:
        if fuel < 1:
            ledger.mark_deposit_dust(address, txh, token=token, token_amount=amt,
                                     note=f"deposit {amt:.6f} {token} < 1 Fuel (dust)",
                                     price_usd=price, usd_value=usd, chain="SOLANA", meta=meta)
            return 0.0
        ledger.credit(address, fuel, kind="deposit", token=token, token_amount=amt, txhash=txh,
                      note=note, price_usd=price, usd_value=usd, chain="SOLANA", meta=meta)
    except ledger.DuplicateDeposit:
        return 0.0
    return fuel


def _receipt_ok(txh: str, cfg: Dict[str, Any]) -> bool:
    try:
        return solana_chain.tx_succeeded(txh, network=cfg["network"])
    except Exception:
        return False


def _process_rows(rows: List[Dict[str, Any]], cfg: Dict[str, Any], user: str | None) -> Dict[str, Any]:
    """Run the four gates over provider rows. `user` set = scoped to that login."""
    credited, points, pending, mine = 0, 0.0, False, False
    for d in rows:
        txh = d.get("txhash")
        amt = float(d.get("amount") or 0)
        if not txh or amt <= 0:
            continue
        owner = user
        if user is None:                                    # background sweep
            owner = _bound_owner(d)
            if not owner:
                ml = (d.get("memo_login") or "").lower()
                if ml and ledger.is_user(ml):
                    owner = ml
            if not owner:
                if d.get("sender_keys"):
                    record_unattributed(cfg, d)
                continue
        elif not belongs_to(d, user):
            continue
        mine = True
        if ledger.seen_txhash(txh):
            continue
        token = (d.get("token") or "SOL").upper()
        conv = _conversion(token)
        if conv["mode"] == "pending":
            log.warning("price unavailable for %s — deposit left pending", token)
            pending = True
            continue
        if not _receipt_ok(txh, cfg):
            pending = True
            continue
        f = credit_deposit(owner, amt, txh, conv["price"], token, d.get("block"), d.get("ts"))
        if f > 0:
            credited += 1
            points += f
    out = {"credited": credited, "credited_points": points, "mine": mine}
    if pending:
        out["pending"] = True
    return out


def credit_signature(user: str, signature: str, cfg: Dict[str, Any] | None = None) -> Dict[str, Any]:
    """The wallet broadcast `signature`: look THAT tx up directly (no
    treasury-wide scan) and credit it if it is a finalized deposit of `user`."""
    cfg = cfg or config.chain_cfg()
    if not cfg.get("enabled"):
        return {"credited": 0, "credited_points": 0.0, "error": True, "reason": "chain_disabled"}
    try:
        rows = solana_chain.fetch_tx(cfg["network"], cfg["treasury"], signature, spl=cfg["spl"])
    except Exception as e:
        log.error("fetch_tx failed: %r", e)
        return {"credited": 0, "credited_points": 0.0, "pending": True, "error": True}
    if not rows:
        return {"credited": 0, "credited_points": 0.0, "pending": True, "found": False}
    return {**_process_rows(rows, cfg, user), "found": True}


def scan_for_user(user: str, limit: int = 40, cfg: Dict[str, Any] | None = None) -> Dict[str, Any]:
    cfg = cfg or config.chain_cfg()
    if not cfg.get("enabled"):
        return {"credited": 0, "credited_points": 0.0, "skipped": "no treasury"}
    try:
        rows = solana_chain.fetch_incoming(cfg["network"], cfg["treasury"], limit=limit,
                                           spl=cfg["spl"], seen=ledger.seen_txhash)
    except Exception as e:
        log.error("scan failed: %r", e)
        return {"credited": 0, "credited_points": 0.0, "error": True}
    out = _process_rows(rows, cfg, user)
    out.pop("mine", None)
    return out


def scan_once(limit: int = 40, cfg: Dict[str, Any] | None = None) -> Dict[str, Any]:
    """Operator-side sweep: credits only senders attributable beyond doubt."""
    cfg = cfg or config.chain_cfg()
    if not cfg.get("enabled"):
        return {"credited": 0, "skipped": "no treasury"}
    try:
        rows = solana_chain.fetch_incoming(cfg["network"], cfg["treasury"], limit=limit,
                                           spl=cfg["spl"], seen=ledger.seen_txhash)
    except Exception as e:
        log.error("sweep failed: %r", e)
        return {"credited": 0, "error": True}
    return {"credited": _process_rows(rows, cfg, None)["credited"]}


# ------------------------------------------------- unattributed deposits ----

def record_unattributed(cfg: Dict[str, Any], d: Dict[str, Any]) -> None:
    txh = (d.get("txhash") or "").strip()
    if not txh or ledger.seen_txhash(txh):
        return
    token = (d.get("token") or "").upper()
    amt = float(d.get("amount") or 0)
    price = pricing.token_price_usd(token)
    # 1-lamport "address poisoning" sends are common on public treasuries.
    if price is not None and amt * price < UNATTRIBUTED_MIN_USD:
        return
    ledger.execute(
        "INSERT OR IGNORE INTO deposit_unattributed"
        "(txhash,chain,token,amount,sender,block,block_ts,first_seen) VALUES(?,?,?,?,?,?,?,?)",
        (txh, cfg.get("id") or "SOLANA", token, amt, str(d.get("sender_sol") or ""),
         int(d.get("block") or 0), int(d.get("ts") or 0), time.time()))


def list_unattributed(limit: int = 100, include_resolved: bool = False) -> List[Dict[str, Any]]:
    where = "" if include_resolved else "WHERE resolved_at=0"
    return ledger.query_all(f"SELECT * FROM deposit_unattributed {where} ORDER BY first_seen DESC LIMIT ?",
                            (max(1, min(int(limit or 100), 500)),))


def attribute_unattributed(txhash: str, address: str, by: str = "operator",
                           cfg: Dict[str, Any] | None = None) -> Dict[str, Any]:
    """Operator attribution: re-verify the tx on-chain, credit `address` through
    the normal path, mark the row resolved. Idempotent."""
    cfg = cfg or config.chain_cfg()
    txh, address = (txhash or "").strip(), (address or "").strip().lower()
    row = ledger.query_one("SELECT * FROM deposit_unattributed WHERE txhash=?", (txh,))
    if not row:
        raise ValueError("unknown txhash")
    if not address or not ledger.is_user(address):
        raise ValueError("address is not a registered login")
    if ledger.seen_txhash(txh):
        ledger.execute("UPDATE deposit_unattributed SET resolved_address=?,resolved_at=?,resolved_by=? "
                       "WHERE txhash=? AND resolved_at=0", (address, time.time(), by, txh))
        return {"credited": 0, "credited_points": 0.0, "already": True}
    rows = solana_chain.fetch_tx(cfg["network"], cfg["treasury"], txh, spl=cfg["spl"])
    if not rows:
        raise ValueError("tx not found on-chain or not a deposit to the treasury")
    total = 0.0
    for d in rows:
        amt = float(d.get("amount") or 0)
        if amt <= 0 or d.get("txhash") != txh:
            continue
        token = (d.get("token") or "SOL").upper()
        price = pricing.token_price_usd(token)
        if not price:
            raise ValueError(f"{token} price unavailable — retry shortly")
        if not _receipt_ok(txh, cfg):
            raise ValueError("tx not finalized on-chain")
        total += credit_deposit(address, amt, txh, price, token, d.get("block"), d.get("ts"))
    ledger.execute("UPDATE deposit_unattributed SET resolved_address=?,resolved_at=?,resolved_by=? WHERE txhash=?",
                   (address, time.time(), by, txh))
    return {"credited": 1 if total > 0 else 0, "credited_points": total}
