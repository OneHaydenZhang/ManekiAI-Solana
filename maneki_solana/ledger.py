"""Gas ledger — per-wallet balance + append-only history on SQLite (WAL).

Invariants (same as production):
  * Every mutation goes through `_apply`, so the balance row and the ledger row
    can never drift apart; each row snapshots `balance_after`.
  * Deposits are idempotent by on-chain signature: a pre-check AND a partial
    UNIQUE index (kind='deposit') — concurrent scans cannot double-credit.
  * Ledger rows are never updated or deleted; corrections are new rows.
  * A charge never creates debt: at zero balance nothing is written.
"""
from __future__ import annotations

import json
import math
import sqlite3
import threading
import time
from typing import Any, Dict, List, Optional

from . import config

_LOCK = threading.RLock()
_conn: Optional[sqlite3.Connection] = None
_conn_path: Optional[str] = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  address TEXT PRIMARY KEY, created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS gas_balance (
  address TEXT PRIMARY KEY, balance REAL NOT NULL DEFAULT 0, updated_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS gas_tx (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  address TEXT NOT NULL, kind TEXT NOT NULL, points REAL NOT NULL,
  token TEXT DEFAULT '', token_amount REAL DEFAULT 0, txhash TEXT DEFAULT '',
  note TEXT DEFAULT '', ts REAL NOT NULL, balance_after REAL NOT NULL,
  agent_id TEXT DEFAULT '', ref TEXT DEFAULT '', price_usd REAL DEFAULT 0,
  usd_value REAL DEFAULT 0, meta_json TEXT DEFAULT '', chain TEXT DEFAULT '');
CREATE INDEX IF NOT EXISTS idx_gas_tx_addr ON gas_tx(address);
CREATE INDEX IF NOT EXISTS idx_gas_tx_hash ON gas_tx(txhash);
CREATE UNIQUE INDEX IF NOT EXISTS uidx_gas_tx_deposit
  ON gas_tx(txhash) WHERE kind='deposit' AND txhash != '';
-- sender key ("sol:<base58 pubkey>", case-SENSITIVE) -> login address.
-- A row can only be written after an ed25519 proof of control.
CREATE TABLE IF NOT EXISTS deposit_senders (
  sender_key TEXT PRIMARY KEY, address TEXT NOT NULL, created_at REAL NOT NULL);
-- Treasury deposits nobody can be credited for yet (exchange withdrawals...).
-- Resolved by an operator; never deleted.
CREATE TABLE IF NOT EXISTS deposit_unattributed (
  txhash TEXT PRIMARY KEY, chain TEXT, token TEXT, amount REAL, sender TEXT,
  block INTEGER DEFAULT 0, block_ts INTEGER DEFAULT 0, first_seen REAL,
  resolved_address TEXT DEFAULT '', resolved_at REAL DEFAULT 0, resolved_by TEXT DEFAULT '');
"""


class DuplicateDeposit(Exception):
    """This on-chain signature was already credited (schema-level dedup)."""


def connect(path: Optional[str] = None) -> sqlite3.Connection:
    global _conn, _conn_path
    with _LOCK:
        if _conn is not None and (path is None or _conn_path == path):
            return _conn            # a bare connect() reuses whatever is open
        path = path or config.db_path()
        if _conn is not None:
            _conn.close()
        if path != ":memory:":
            import os
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        c = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        c.executescript(SCHEMA)
        _conn, _conn_path = c, path
        return c


def reset(path: str = ":memory:") -> None:
    """Tests: drop the connection and start from a fresh database."""
    global _conn, _conn_path
    with _LOCK:
        if _conn is not None:
            _conn.close()
        _conn = _conn_path = None
    connect(path)


def query_one(sql: str, params: tuple = ()) -> Optional[Dict[str, Any]]:
    with _LOCK:
        r = connect().execute(sql, params).fetchone()
    return dict(r) if r else None


def query_all(sql: str, params: tuple = ()) -> List[Dict[str, Any]]:
    with _LOCK:
        return [dict(r) for r in connect().execute(sql, params).fetchall()]


def execute(sql: str, params: tuple = ()) -> None:
    with _LOCK:
        connect().execute(sql, params)


# ------------------------------------------------------------- users --------

def ensure_user(address: str) -> None:
    execute("INSERT OR IGNORE INTO users(address, created_at) VALUES(?,?)",
            ((address or "").lower(), time.time()))


def is_user(address: str) -> bool:
    return query_one("SELECT 1 FROM users WHERE address=?", ((address or "").lower(),)) is not None


# ------------------------------------------------------------ balance -------

def balance(address: str) -> float:
    row = query_one("SELECT balance FROM gas_balance WHERE address=?", ((address or "").lower(),))
    return float(row["balance"]) if row else 0.0


def _apply(address: str, delta: float, kind: str, *, token: str = "", token_amount: float = 0.0,
           txhash: str = "", note: str = "", agent_id: str = "", ref: str = "",
           price_usd: float = 0.0, usd_value: float = 0.0, chain: str = "",
           meta: Optional[Dict[str, Any]] = None, floor_at_zero: bool = False) -> float:
    address = (address or "").lower()
    now = time.time()
    meta = dict(meta or {})
    with _LOCK:
        c = connect()
        cur = balance(address)
        new = cur + delta
        if new < -1e-9:
            if not floor_at_zero or delta >= 0:
                raise ValueError("insufficient gas")
            meta["shortfall"] = round(-new, 6)
            delta, new = -cur, 0.0
        new = max(0.0, new)
        c.execute("BEGIN")
        try:
            c.execute("INSERT INTO gas_balance(address,balance,updated_at) VALUES(?,?,?) "
                      "ON CONFLICT(address) DO UPDATE SET balance=excluded.balance, updated_at=excluded.updated_at",
                      (address, new, now))
            c.execute("INSERT INTO gas_tx(address,kind,points,token,token_amount,txhash,note,ts,"
                      "balance_after,agent_id,ref,price_usd,usd_value,meta_json,chain) "
                      "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                      (address, kind, delta, token or "", float(token_amount or 0), txhash or "",
                       note or "", now, new, agent_id or "", ref or "", float(price_usd or 0),
                       float(usd_value or 0), json.dumps(meta, default=str) if meta else "", chain or ""))
            c.execute("COMMIT")
        except sqlite3.IntegrityError:
            c.execute("ROLLBACK")          # balance row rolled back with the ledger row
            raise DuplicateDeposit(txhash)
        except Exception:
            c.execute("ROLLBACK")
            raise
    return new


def credit(address: str, gas: float, kind: str = "grant", **kw) -> float:
    gas = math.floor(float(gas))              # whole numbers only
    if gas <= 0:
        raise ValueError("gas must be > 0")
    return _apply(address, float(gas), kind, **kw)


def mark_deposit_dust(address: str, txhash: str, **kw) -> None:
    """On-chain dust worth < 1 Gas: zero-point row so the signature enters the
    dedup index and is never re-scanned."""
    if not txhash:
        return
    try:
        _apply(address, 0.0, "deposit", txhash=txhash, **kw)
    except DuplicateDeposit:
        pass


def try_debit(address: str, gas: float, kind: str = "spend", **kw) -> bool:
    """Pre-paid spend (hot path). False when the balance is too low."""
    try:
        _apply(address, -abs(float(gas)), kind, **kw)
        return True
    except ValueError:
        return False


def charge(address: str, gas: float, kind: str, **kw) -> float:
    """Post-service charge. A raced-away balance charges down to 0 with the
    shortfall recorded; at zero balance nothing is written (no debt, ever)."""
    address = (address or "").lower()
    if gas <= 0:
        return balance(address)
    with _LOCK:
        if balance(address) <= 1e-9:
            return 0.0
        return _apply(address, -abs(float(gas)), kind, floor_at_zero=True, **kw)


def seen_txhash(txhash: str) -> bool:
    return bool(txhash) and query_one("SELECT 1 FROM gas_tx WHERE txhash=?", (txhash,)) is not None


def history(address: str, limit: int = 50) -> List[Dict[str, Any]]:
    rows = query_all("SELECT * FROM gas_tx WHERE address=? ORDER BY id DESC LIMIT ?",
                     ((address or "").lower(), max(1, min(int(limit), 500))))
    for r in rows:
        r["meta"] = json.loads(r.pop("meta_json") or "{}") if r.get("meta_json") else {}
        r.pop("meta_json", None)
    return rows


def deposited_usd_total(address: str) -> float:
    row = query_one("SELECT COALESCE(SUM(usd_value),0) AS s FROM gas_tx WHERE address=? AND kind='deposit'",
                    ((address or "").lower(),))
    return float(row["s"]) if row else 0.0
