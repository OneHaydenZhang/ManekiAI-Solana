"""Solana — SOL / USDC / USDT deposits for Gas top-ups. Registered in
the chain registry as provider "solana" (design: docs/SOLANA_DEPOSIT.md).

The first non-EVM, non-Cosmos lane. Three things differ from the EVM lanes and
everything else reuses the shared deposit pipeline unchanged:

  • Signing: the server BUILDS the unsigned transaction (solders, lazily
    imported — the service boots and scans without it), the user's wallet
    signs+broadcasts it (Wallet Standard takes raw bytes, no browser lib),
    and the server confirms by signature (`fetch_tx`).
  • Token accounts: SPL balances live in per-mint token accounts owned by the
    treasury, not at the treasury address itself. The scanner watches the
    treasury AND every registered mint's token account (resolved by RPC,
    cached) and parses deposits from pre/post balance deltas — robust to
    CPI/inner instructions and batching.
  • Attribution: the depositing key is ed25519, unrelated to the EVM login
    key. Rows carry `sender_keys` (["sol:<pubkey>"], matched against
    deposit_senders bindings proven by `verify_bind_signature`) and
    `memo_login` (the "maneki:<0x>" memo our UI attaches).

Pure JSON-RPC over httpx for every read. Only REGISTERED mints are ever
credited; `meta.err` must be null; signatures are the txhash (dedup index).
Any RPC failure raises and the scan simply retries next refresh.
"""
from __future__ import annotations

import base64
import os
import re
import threading
import time
from collections import OrderedDict
from typing import Any, Callable, Dict, List, Optional, Tuple

import httpx

KIND = "solana"
NATIVE_DECIMALS = 9
LAMPORTS = 10 ** 9

# Public gateways, tried in order. The Solana Foundation endpoint rate-limits
# hard (429 on a 10-call batch, measured live 2026-09-30); publicnode is the
# same kind of public fallback the EVM lanes use (dRPC). Operators should set
# SOLANA_RPC_URLS to a keyed gateway (Helius/QuickNode free tier) — the admin
# console flags when they haven't.
MAINNET_RPCS: List[str] = ["https://api.mainnet-beta.solana.com",
                           "https://solana-rpc.publicnode.com"]
DEVNET_RPCS: List[str] = ["https://api.devnet.solana.com"]

EXPLORER = "https://solscan.io"
EXPLORER_TX_PREFIX = "https://solscan.io/tx/"

SYSTEM_PROGRAM = "11111111111111111111111111111111"
TOKEN_PROGRAM = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
ATA_PROGRAM = "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL"
MEMO_PROGRAM = "MemoSq4gqABAXKb96qnH8TysNcWxMyWCqXgDLGmfcHr"
MEMO_PREFIX = "maneki:"
_MEMO_RE = re.compile(r"^\s*maneki:(0[xX][0-9a-fA-F]{40})\s*$")

# Devnet has no official USDT; Circle's devnet USDC is the only side-token
# available for the testnet lane (verified live 2026-09-30, decimals 6).
DEVNET_MINTS = {"USDC": "4zMMC9srt5Ri5X14GAgXhaHii3GnPAEERYPJgZJDncDU"}

_RPC_TIMEOUT = 15.0
_BATCH = 5                     # getTransaction calls per JSON-RPC batch
_DETAIL_PAUSE_S = 0.25         # spacing between single getTransaction calls (public RPC)
_TX_CACHE_MAX = 2000           # parsed-tx LRU (signature -> rows)
_TA_CACHE_TTL = 600.0          # treasury token-account lookups


class ChainError(Exception):
    pass


# ------------------------------------------------------------- base58 -------
# Pure-Python base58 (Bitcoin alphabet) so registry validation never depends
# on the optional solders wheel.
_B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
_B58_INDEX = {c: i for i, c in enumerate(_B58)}


def b58decode(s: str) -> bytes:
    s = (s or "").strip()
    if not s:
        return b""
    n = 0
    for ch in s:
        if ch not in _B58_INDEX:
            raise ValueError("invalid base58 character")
        n = n * 58 + _B58_INDEX[ch]
    body = n.to_bytes((n.bit_length() + 7) // 8, "big") if n else b""
    pad = len(s) - len(s.lstrip("1"))
    return b"\x00" * pad + body


def b58encode(b: bytes) -> str:
    n = int.from_bytes(b, "big")
    out = ""
    while n:
        n, r = divmod(n, 58)
        out = _B58[r] + out
    pad = len(b) - len(b.lstrip(b"\x00"))
    return "1" * pad + out


def is_pubkey(s: str) -> bool:
    try:
        return len(b58decode(s)) == 32
    except ValueError:
        return False


# ---------------------------------------------------------------- RPC -------

def rpc_urls(network: str) -> List[str]:
    if (network or "mainnet") == "testnet":
        raw = os.environ.get("SOLANA_DEVNET_RPC_URLS", "").strip()
        return [u.strip() for u in raw.split(",") if u.strip()] or list(DEVNET_RPCS)
    raw = os.environ.get("SOLANA_RPC_URLS", "").strip()
    return [u.strip() for u in raw.split(",") if u.strip()] or list(MAINNET_RPCS)


def rpc_is_keyed(network: str = "mainnet") -> bool:
    """True when the operator configured their own gateway (admin hint)."""
    urls = rpc_urls(network)
    return bool(urls) and urls[0] not in MAINNET_RPCS + DEVNET_RPCS


def _post(url: str, payload: Any) -> Any:
    r = httpx.post(url, json=payload, timeout=_RPC_TIMEOUT)
    r.raise_for_status()
    return r.json()


def _rpc(url: str, method: str, params: list) -> Any:
    body = _post(url, {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}) or {}
    if isinstance(body, dict) and body.get("error"):
        raise RuntimeError(f"{method}: {body['error']}")
    return body.get("result") if isinstance(body, dict) else None


def _rpc_batch(url: str, calls: List[Tuple[str, list]]) -> List[Any]:
    """JSON-RPC batch; results returned in call order (None for errors)."""
    if not calls:
        return []
    payload = [{"jsonrpc": "2.0", "id": i, "method": m, "params": p} for i, (m, p) in enumerate(calls)]
    body = _post(url, payload)
    out: List[Any] = [None] * len(calls)
    if isinstance(body, dict):          # some gateways refuse batches → single error object
        raise RuntimeError(f"batch: {body.get('error') or body}")
    for item in body or []:
        try:
            idx = int(item.get("id"))
        except (TypeError, ValueError):
            continue
        if 0 <= idx < len(out) and not item.get("error"):
            out[idx] = item.get("result")
    return out


def _call(network: str, method: str, params: list) -> Any:
    last: Exception | None = None
    for url in rpc_urls(network):
        try:
            return _rpc(url, method, params)
        except Exception as e:
            last = e
    raise ChainError(f"solana rpc failed on all endpoints ({method}): {last!r}")


def _call_batch(network: str, calls: List[Tuple[str, list]]) -> List[Any]:
    last: Exception | None = None
    for url in rpc_urls(network):
        try:
            return _rpc_batch(url, calls)
        except Exception as e:
            last = e
    raise ChainError(f"solana rpc batch failed on all endpoints: {last!r}")


# --------------------------------------------------------- UI params --------

def ui_params(network: str) -> Dict[str, Any]:
    """What the deposit frontend needs for a kind:"solana" chain (served via
    chains.public_list). No RPC URL is exposed — the wallet uses its own."""
    dev = (network or "mainnet") == "testnet"
    return {
        "cluster": "devnet" if dev else "mainnet-beta",
        "chain": "solana:devnet" if dev else "solana:mainnet",   # Wallet Standard chain id
        "explorer": EXPLORER,
        "explorer_tx": EXPLORER_TX_PREFIX,
        "explorer_suffix": "?cluster=devnet" if dev else "",
        "symbol": "SOL",
        "decimals": NATIVE_DECIMALS,
        "memo_prefix": MEMO_PREFIX,
    }


def effective_mints(network: str, spl: Dict[str, Dict[str, Any]] | None) -> Dict[str, Dict[str, Any]]:
    """Registry side-tokens for this network. On devnet the mainnet mints do
    not exist: registered symbols are remapped to their devnet mint (USDC)
    and the rest are dropped — the lane never watches a mint that can't
    exist on the cluster it scans."""
    spl = dict(spl or {})
    if (network or "mainnet") != "testnet":
        return spl
    out: Dict[str, Dict[str, Any]] = {}
    for sym, tc in spl.items():
        if sym in DEVNET_MINTS:
            out[sym] = {**tc, "mint": DEVNET_MINTS[sym]}
    return out


# ------------------------------------------------ token accounts ------------
_ta_lock = threading.Lock()
_ta_cache: Dict[Tuple[str, str, str], Tuple[List[str], float]] = {}


def token_accounts(network: str, owner: str, mint: str) -> List[str]:
    """Token accounts of `owner` for `mint` (usually just the ATA). RPC-resolved
    rather than derived: no curve math, and non-ATA accounts are covered."""
    key = (network, owner, mint)
    now = time.time()
    with _ta_lock:
        ent = _ta_cache.get(key)
        if ent and now - ent[1] < _TA_CACHE_TTL:
            return list(ent[0])
    res = _call(network, "getTokenAccountsByOwner",
                [owner, {"mint": mint}, {"encoding": "jsonParsed", "commitment": "finalized"}]) or {}
    accts = [str(v.get("pubkey")) for v in (res.get("value") or []) if v.get("pubkey")]
    with _ta_lock:
        _ta_cache[key] = (accts, now)
    return list(accts)


def invalidate_token_account_cache() -> None:
    with _ta_lock:
        _ta_cache.clear()


# ------------------------------------------------------- parsing ------------

def _acct_keys(msg: Dict[str, Any]) -> List[str]:
    keys = []
    for k in msg.get("accountKeys") or []:
        keys.append(str(k.get("pubkey")) if isinstance(k, dict) else str(k))
    return keys


def _all_instructions(tx: Dict[str, Any]) -> List[Dict[str, Any]]:
    msg = (tx.get("transaction") or {}).get("message") or {}
    out = list(msg.get("instructions") or [])
    for inner in ((tx.get("meta") or {}).get("innerInstructions") or []):
        out.extend(inner.get("instructions") or [])
    return out


def parse_transaction(tx: Dict[str, Any], treasury: str,
                      spl: Dict[str, Dict[str, Any]] | None = None) -> List[Dict[str, Any]]:
    """One jsonParsed transaction -> deposit rows TO `treasury`:
    [{txhash, sender_sol, sender_eth:"", sender_keys, memo_login, amount,
      token, block(slot), ts}]. Failed txs and unregistered mints yield
    nothing. Amounts are pre/post balance deltas (SOL: lamports of the
    treasury key; SPL: token accounts owned by the treasury per mint)."""
    if not tx:
        return []
    meta = tx.get("meta") or {}
    if tx.get("meta") is None or meta.get("err") is not None:
        return []
    txn = tx.get("transaction") or {}
    msg = txn.get("message") or {}
    sigs = txn.get("signatures") or []
    txh = str(sigs[0]) if sigs else ""
    if not txh:
        return []
    keys = _acct_keys(msg)
    fee_payer = keys[0] if keys else ""
    slot = tx.get("slot")
    ts = tx.get("blockTime")
    by_mint = {str(tc.get("mint") or ""): (sym, int(tc.get("decimals") or 6))
               for sym, tc in (spl or {}).items() if tc.get("mint")}
    instrs = _all_instructions(tx)

    # memo → login attribution hint
    memo_login = ""
    for ins in instrs:
        prog = ins.get("program") or ""
        pid = ins.get("programId") or ""
        if prog == "spl-memo" or pid == MEMO_PROGRAM:
            parsed = ins.get("parsed")
            if isinstance(parsed, str):
                m = _MEMO_RE.match(parsed)
                if m:
                    memo_login = m.group(1).lower()
                    break

    rows: List[Dict[str, Any]] = []
    base = {"txhash": txh, "sender_eth": "", "memo_login": memo_login}
    if slot is not None:
        try:
            base["block"] = int(slot)
        except (TypeError, ValueError):
            pass
    if ts is not None:
        try:
            base["ts"] = int(ts)
        except (TypeError, ValueError):
            pass

    # --- native SOL: lamport delta of the treasury key -----------------------
    if treasury in keys:
        i = keys.index(treasury)
        pre, post = meta.get("preBalances") or [], meta.get("postBalances") or []
        if i < len(pre) and i < len(post):
            try:
                delta = int(post[i]) - int(pre[i])
            except (TypeError, ValueError):
                delta = 0
            if delta > 0:
                sender = ""
                for ins in instrs:
                    if (ins.get("program") == "system" and isinstance(ins.get("parsed"), dict)
                            and ins["parsed"].get("type") in ("transfer", "transferWithSeed")):
                        info = ins["parsed"].get("info") or {}
                        if info.get("destination") == treasury:
                            sender = str(info.get("source") or "")
                            break
                sender = sender or fee_payer
                if sender and sender != treasury:
                    rows.append({**base, "sender_sol": sender, "sender_keys": [f"sol:{sender}"],
                                 "amount": delta / LAMPORTS, "token": "SOL"})

    # --- SPL: per-mint delta of treasury-owned token accounts ----------------
    if by_mint:
        def _tb(lst):
            out: Dict[int, Tuple[str, str, int]] = {}
            for b in lst or []:
                try:
                    amt = int(((b.get("uiTokenAmount") or {}).get("amount")) or 0)
                except (TypeError, ValueError):
                    amt = 0
                out[int(b.get("accountIndex", -1))] = (str(b.get("mint") or ""), str(b.get("owner") or ""), amt)
            return out
        pre_tb, post_tb = _tb(meta.get("preTokenBalances")), _tb(meta.get("postTokenBalances"))
        gained: Dict[str, int] = {}
        lost: Dict[str, List[Tuple[int, str]]] = {}
        for idx in set(pre_tb) | set(post_tb):
            mint, owner, post_amt = post_tb.get(idx, pre_tb.get(idx, ("", "", 0)))
            if mint not in by_mint:
                continue
            pre_amt = pre_tb.get(idx, (mint, owner, 0))[2]
            d = post_amt - pre_amt
            if owner == treasury and d > 0:
                gained[mint] = gained.get(mint, 0) + d
            elif owner and owner != treasury and d < 0:
                lost.setdefault(mint, []).append((d, owner))
        for mint, raw in gained.items():
            sym, dec = by_mint[mint]
            senders = sorted(lost.get(mint) or [])       # most negative first
            sender = senders[0][1] if senders else fee_payer
            if not sender or sender == treasury:
                continue
            rows.append({**base, "sender_sol": sender, "sender_keys": [f"sol:{sender}"],
                         "amount": raw / (10 ** dec), "token": sym})
    return rows


# ------------------------------------------------------ tx cache ------------
_tx_lock = threading.Lock()
_tx_cache: "OrderedDict[str, List[Dict[str, Any]]]" = OrderedDict()
# Negative cache: signatures the gateways returned `null` for (history the
# public fallback node no longer serves, or a tx not yet finalized). Skipped
# for a while so every scan does not re-pay for the same misses.
_null_cache: Dict[str, float] = {}
_NULL_TTL_S = 600.0


def _null_seen(sig: str) -> bool:
    with _tx_lock:
        at = _null_cache.get(sig)
        return bool(at) and time.time() - at < _NULL_TTL_S


def _null_put(sig: str) -> None:
    with _tx_lock:
        _null_cache[sig] = time.time()
        if len(_null_cache) > _TX_CACHE_MAX:
            for k in list(_null_cache)[: len(_null_cache) - _TX_CACHE_MAX]:
                _null_cache.pop(k, None)


def _cache_get(sig: str) -> Optional[List[Dict[str, Any]]]:
    with _tx_lock:
        v = _tx_cache.get(sig)
        if v is not None:
            _tx_cache.move_to_end(sig)
        return v


def _cache_put(sig: str, rows: List[Dict[str, Any]]) -> None:
    with _tx_lock:
        _tx_cache[sig] = rows
        _tx_cache.move_to_end(sig)
        while len(_tx_cache) > _TX_CACHE_MAX:
            _tx_cache.popitem(last=False)


def _tx_params(sig: str) -> list:
    return [sig, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0,
                  "commitment": "finalized"}]


# ------------------------------------------------------- scan legs ----------

def watched_accounts(network: str, treasury: str,
                     spl: Dict[str, Dict[str, Any]] | None) -> Dict[str, List[str]]:
    """treasury + each registered mint's token accounts: {symbol|"SOL": [accounts]}"""
    out: Dict[str, List[str]] = {"SOL": [treasury]}
    for sym, tc in effective_mints(network, spl).items():
        if tc.get("mint"):
            out[sym] = token_accounts(network, treasury, tc["mint"])
    return out


def fetch_incoming(network: str, treasury: str, limit: int = 40,
                   spl: Dict[str, Dict[str, Any]] | None = None,
                   seen: Optional[Callable[[str], bool]] = None) -> List[Dict[str, Any]]:
    """Recent deposits TO the treasury (SOL + registered SPL mints). `limit`
    = signatures per watched account. `seen(sig)` (the ledger's dedup
    check, passed by deposits._fetch_incoming) skips fetching details of
    already-credited txs — on Solana every tx costs one getTransaction."""
    treasury = (treasury or "").strip()
    if not treasury:
        return []
    mints = effective_mints(network, spl)
    accts: List[str] = []
    for lst in watched_accounts(network, treasury, mints).values():
        for a in lst:
            if a not in accts:
                accts.append(a)
    sigs: "OrderedDict[str, None]" = OrderedDict()
    for a in accts:
        res = _call(network, "getSignaturesForAddress",
                    [a, {"limit": max(1, min(int(limit), 1000)), "commitment": "finalized"}]) or []
        for s in res:
            sig = str(s.get("signature") or "")
            if sig and s.get("err") is None:
                sigs.setdefault(sig, None)
    todo = [s for s in sigs if _cache_get(s) is None and not _null_seen(s) and not (seen and seen(s))]
    _fetch_details(network, treasury, mints, todo)
    rows: List[Dict[str, Any]] = []
    for sig in sigs:
        cached = _cache_get(sig)
        if cached:
            rows.extend(cached)
    return rows


def _fetch_details(network: str, treasury: str, mints: Dict[str, Dict[str, Any]],
                   todo: List[str]) -> None:
    """Fill the parsed-tx cache for `todo` signatures. Batches first; when a
    gateway refuses batches or rate-limits (429 — the public Foundation RPC
    does, measured live), falls back to spaced single calls with one retry.
    A signature that still can't be fetched is simply left for the next scan
    (nothing is credited without its details, nothing is ever lost — the
    ledger dedup is by signature)."""
    for i in range(0, len(todo), _BATCH):
        chunk = todo[i:i + _BATCH]
        results: List[Any] | None = None
        try:
            results = _call_batch(network, [("getTransaction", _tx_params(s)) for s in chunk])
        except Exception:
            results = None
        if results is not None:
            for sig, tx in zip(chunk, results):
                if tx is not None:
                    _cache_put(sig, parse_transaction(tx, treasury, mints))
                else:
                    _null_put(sig)
            continue
        for sig in chunk:                      # degraded path: one at a time
            tx = None
            for attempt in range(2):
                try:
                    tx = _call(network, "getTransaction", _tx_params(sig))
                    break
                except Exception:
                    time.sleep(1.0 + attempt)
            if tx is not None:
                _cache_put(sig, parse_transaction(tx, treasury, mints))
            else:
                _null_put(sig)
            time.sleep(_DETAIL_PAUSE_S)


def fetch_tx(network: str, treasury: str, signature: str,
             spl: Dict[str, Dict[str, Any]] | None = None) -> List[Dict[str, Any]]:
    """Single-signature lookup (the post-send confirm path). Returns the same
    row shape as fetch_incoming; [] when not (yet) finalized or not a
    deposit to the treasury."""
    sig = (signature or "").strip()
    if not sig:
        return []
    cached = _cache_get(sig)
    if cached:
        return list(cached)
    tx = _call(network, "getTransaction", _tx_params(sig))
    if not tx:
        return []
    rows = parse_transaction(tx, treasury, effective_mints(network, spl))
    _cache_put(sig, rows)
    return list(rows)


def tx_succeeded(txhash: str, network: str = "mainnet") -> bool:
    """Only a FINALIZED, error-free signature counts. Any RPC trouble → False
    (the scan retries; we never credit on uncertainty).

    Short-circuit: a signature whose parsed rows sit in the tx cache was
    fetched with commitment=finalized and had meta.err == null (errored txs
    parse to no rows) — that IS the receipt, so no extra call is spent on a
    rate-limited public gateway."""
    cached = _cache_get(txhash)
    if cached:
        return True
    try:
        res = _call(network, "getSignatureStatuses", [[txhash], {"searchTransactionHistory": True}]) or {}
        st = ((res.get("value") or [None])[0]) or {}
        return bool(st) and st.get("err") is None and st.get("confirmationStatus") == "finalized"
    except Exception:
        return False


# ------------------------------------------------------- balances -----------

def get_sol_balance(network: str, pubkey: str) -> float:
    res = _call(network, "getBalance", [pubkey, {"commitment": "confirmed"}]) or {}
    return int(res.get("value") or 0) / LAMPORTS


def get_token_balance(network: str, owner: str, mint: str) -> Tuple[float, str]:
    """(balance, best token account) of `owner` for `mint` — the account
    holding the most is the transfer source."""
    res = _call(network, "getTokenAccountsByOwner",
                [owner, {"mint": mint}, {"encoding": "jsonParsed", "commitment": "confirmed"}]) or {}
    best_amt, best_acct, total = -1, "", 0.0
    for v in res.get("value") or []:
        info = ((((v.get("account") or {}).get("data") or {}).get("parsed") or {}).get("info") or {})
        ta = info.get("tokenAmount") or {}
        try:
            raw = int(ta.get("amount") or 0)
            ui = float(ta.get("uiAmount") or 0.0)
        except (TypeError, ValueError):
            continue
        total += ui
        if raw > best_amt:
            best_amt, best_acct = raw, str(v.get("pubkey") or "")
    return total, best_acct


# ------------------------------------------------------- build leg ----------

def _solders():
    try:
        from solders.pubkey import Pubkey                       # noqa: F401
        from solders.instruction import Instruction, AccountMeta  # noqa: F401
        from solders.message import Message                     # noqa: F401
        from solders.transaction import Transaction             # noqa: F401
        from solders.hash import Hash                           # noqa: F401
        from solders.system_program import transfer, TransferParams  # noqa: F401
    except Exception as e:  # pragma: no cover - depends on the optional wheel
        raise ChainError(f"solana build unavailable (solders missing): {e!r}")
    return Pubkey, Instruction, AccountMeta, Message, Transaction, Hash, transfer, TransferParams


def derive_ata(owner: str, mint: str) -> str:
    Pubkey = _solders()[0]
    ata, _ = Pubkey.find_program_address(
        [bytes(Pubkey.from_string(owner)), bytes(Pubkey.from_string(TOKEN_PROGRAM)),
         bytes(Pubkey.from_string(mint))], Pubkey.from_string(ATA_PROGRAM))
    return str(ata)


def deposit_build(network: str, sender: str, treasury: str, amount: float,
                  token_cfg: Dict[str, Any] | None = None, memo: str = "") -> Dict[str, Any]:
    """Unsigned transfer sender → treasury: native SOL (SystemProgram) or an
    SPL transferChecked (token_cfg = {mint, decimals}) — plus the attribution
    memo. Returns bytes for both wallet APIs: `tx_b64` (full serialized tx with
    an empty signature slot, Wallet Standard) and `message_b58` (Phantom's
    request({method:"signAndSendTransaction"}) form)."""
    Pubkey, Instruction, AccountMeta, Message, Transaction, Hash, transfer, TransferParams = _solders()
    if not is_pubkey(sender) or not is_pubkey(treasury):
        raise ChainError("invalid sender/treasury pubkey")
    if amount <= 0:
        raise ChainError("amount must be > 0")
    payer = Pubkey.from_string(sender)
    tre = Pubkey.from_string(treasury)
    ixs = []
    if token_cfg and token_cfg.get("mint"):
        mint_s = str(token_cfg["mint"])
        dec = int(token_cfg.get("decimals") or 6)
        raw = int(round(amount * (10 ** dec)))
        if raw <= 0:
            raise ChainError("amount below token precision")
        mint = Pubkey.from_string(mint_s)
        _, src = get_token_balance(network, sender, mint_s)
        if not src:
            raise ChainError("sender holds no token account for this mint")
        dests = token_accounts(network, treasury, mint_s)
        if dests:
            dest_s = dests[0]
        else:
            # Treasury ATA missing: create it idempotently inside the same tx
            # (payer = sender, ~0.002 SOL rent). Ops should pre-create ATAs
            # (admin card shows the state) so users never pay this.
            dest_s = derive_ata(treasury, mint_s)
            ixs.append(Instruction(
                Pubkey.from_string(ATA_PROGRAM), bytes([1]),
                [AccountMeta(payer, True, True), AccountMeta(Pubkey.from_string(dest_s), False, True),
                 AccountMeta(tre, False, False), AccountMeta(mint, False, False),
                 AccountMeta(Pubkey.from_string(SYSTEM_PROGRAM), False, False),
                 AccountMeta(Pubkey.from_string(TOKEN_PROGRAM), False, False)]))
        # SPL Token TransferChecked: tag 12, u64 amount LE, u8 decimals.
        data = bytes([12]) + raw.to_bytes(8, "little") + bytes([dec])
        ixs.append(Instruction(
            Pubkey.from_string(TOKEN_PROGRAM), data,
            [AccountMeta(Pubkey.from_string(src), False, True), AccountMeta(mint, False, False),
             AccountMeta(Pubkey.from_string(dest_s), False, True), AccountMeta(payer, True, False)]))
        token = str(token_cfg.get("symbol") or "")
    else:
        lamports = int(round(amount * LAMPORTS))
        if lamports <= 0:
            raise ChainError("amount below lamport precision")
        ixs.append(transfer(TransferParams(from_pubkey=payer, to_pubkey=tre, lamports=lamports)))
        token = "SOL"
    if memo:
        ixs.append(Instruction(Pubkey.from_string(MEMO_PROGRAM), memo.encode("utf-8"),
                               [AccountMeta(payer, True, False)]))
    bh = _call(network, "getLatestBlockhash", [{"commitment": "finalized"}]) or {}
    val = bh.get("value") or {}
    blockhash = str(val.get("blockhash") or "")
    if not blockhash:
        raise ChainError("no recent blockhash")
    msg = Message.new_with_blockhash(ixs, payer, Hash.from_string(blockhash))
    tx = Transaction.new_unsigned(msg)
    return {
        "tx_b64": base64.b64encode(bytes(tx)).decode(),
        "message_b58": b58encode(bytes(msg)),
        "blockhash": blockhash,
        "last_valid_block_height": val.get("lastValidBlockHeight"),
        "token": token,
        "memo": memo,
    }


# ------------------------------------------------------- bind proof ---------

def verify_bind_signature(pubkey_b58: str, message: str, signature_b64: str) -> bool:
    """ed25519 proof that the caller controls `pubkey` (wallet signMessage).
    Uses the already-present `cryptography` dependency — no solders."""
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        pk = Ed25519PublicKey.from_public_bytes(b58decode(pubkey_b58))
        sig = base64.b64decode(signature_b64 or "")
        pk.verify(sig, (message or "").encode("utf-8"))
        return True
    except Exception:
        return False
