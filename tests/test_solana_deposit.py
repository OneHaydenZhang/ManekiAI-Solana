"""Solana deposit -> Fuel pipeline. Everything on-chain is a fixed jsonParsed
fixture; no network access is needed.   Run:  python -m pytest tests -q"""
from __future__ import annotations

import base64

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization
from fastapi.testclient import TestClient

from maneki_solana import config, deposits, ledger, pricing, solana_chain
from maneki_solana.server import app, _sessions

ME = "0x1111111111111111111111111111111111111111"
OTHER = "0x2222222222222222222222222222222222222222"
TREASURY = "3FHqTpYb3g8MWpwGfspTyBd3Twfj2X7pKawBg9T8QQSo"
TREASURY_USDC_ATA = "9HJ7qsFrKf2rLbxk5Nv4zHXe3S9vQz9y1s1QkYxg8Kux"
SENDER = "5oNDL3swdJJF1g9DzJiZ4ynHXgszjAEpUkxVYejchzrY"
SENDER_USDC_ATA = "GdW9W7nK1yyzgT8xR2R7ZkLpP2m8VHxDp7yqjT1KQd3x"
USDC = config.SPL_TOKENS["USDC"]["mint"]
FAKE_MINT = "So11111111111111111111111111111111111111112"
SPL = config.SPL_TOKENS
SIG_SOL = "5VERv8NMvzbJMEkV8xnrLkEaWRtSz9CosKDYjCJjBRnbJLgp8uirBgmQpjKhoR4tjF3ZpRzrFmBV6UjKdiSZkQUW"
SIG_USDC = "2nBhEBYYvfaAe16UMNqRHre4YNSskvuYgx3M6E4JP1oDYdodMuJapx1kHMuXpMUUbqDe5FyhTWrL2yYD7e9wDxdx"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    ledger.reset(":memory:")
    monkeypatch.setenv("SOLANA_TREASURY_ADDRESS", TREASURY)
    monkeypatch.setenv("SOL_USD_OVERRIDE", "100")
    monkeypatch.setattr(solana_chain, "tx_succeeded", lambda *a, **k: True)
    ledger.ensure_user(ME)
    ledger.ensure_user(OTHER)
    _sessions.clear()
    yield


def sol_tx(sig=SIG_SOL, lamports=2_000_000_000, memo=None, err=None, sender=SENDER):
    ixs = [{"program": "system", "programId": solana_chain.SYSTEM_PROGRAM,
            "parsed": {"type": "transfer", "info": {"source": sender, "destination": TREASURY, "lamports": lamports}}}]
    if memo:
        ixs.append({"program": "spl-memo", "programId": solana_chain.MEMO_PROGRAM, "parsed": memo})
    return {"slot": 123, "blockTime": 1_700_000_000,
            "transaction": {"signatures": [sig], "message": {"accountKeys": [
                {"pubkey": sender}, {"pubkey": TREASURY}], "instructions": ixs}},
            "meta": {"err": err, "preBalances": [5 * 10**9, 10**9], "postBalances": [3 * 10**9, 10**9 + lamports],
                     "preTokenBalances": [], "postTokenBalances": [], "innerInstructions": []}}


def usdc_tx(sig=SIG_USDC, raw=25_000_000, mint=USDC, memo=None):
    ixs = [{"program": "spl-memo", "programId": solana_chain.MEMO_PROGRAM, "parsed": memo}] if memo else []
    tb = lambda idx, owner, amt: {"accountIndex": idx, "mint": mint, "owner": owner, "uiTokenAmount": {"amount": str(amt)}}
    return {"slot": 124, "blockTime": 1_700_000_100,
            "transaction": {"signatures": [sig], "message": {"accountKeys": [
                {"pubkey": SENDER}, {"pubkey": SENDER_USDC_ATA}, {"pubkey": TREASURY_USDC_ATA}], "instructions": ixs}},
            "meta": {"err": None, "preBalances": [10**9] * 3, "postBalances": [10**9] * 3,
                     "preTokenBalances": [tb(1, SENDER, 100_000_000), tb(2, TREASURY, 0)],
                     "postTokenBalances": [tb(1, SENDER, 100_000_000 - raw), tb(2, TREASURY, raw)],
                     "innerInstructions": []}}


# ------------------------------------------------------------ parsing ------

def test_parse_native_sol():
    rows = solana_chain.parse_transaction(sol_tx(), TREASURY, SPL)
    assert len(rows) == 1
    r = rows[0]
    assert (r["token"], r["amount"], r["sender_sol"], r["txhash"], r["block"]) == ("SOL", 2.0, SENDER, SIG_SOL, 123)
    assert r["sender_keys"] == [f"sol:{SENDER}"]


def test_parse_spl_usdc_uses_token_balance_delta():
    rows = solana_chain.parse_transaction(usdc_tx(), TREASURY, SPL)
    assert [(r["token"], r["amount"], r["sender_sol"]) for r in rows] == [("USDC", 25.0, SENDER)]


def test_parse_ignores_failed_tx_and_unregistered_mint():
    assert solana_chain.parse_transaction(sol_tx(err={"InstructionError": [0, "Custom"]}), TREASURY, SPL) == []
    assert solana_chain.parse_transaction(usdc_tx(mint=FAKE_MINT), TREASURY, SPL) == []


def test_parse_memo_login():
    rows = solana_chain.parse_transaction(sol_tx(memo=f"maneki:{ME.upper().replace('0X', '0x')}"), TREASURY, SPL)
    assert rows[0]["memo_login"] == ME
    assert solana_chain.parse_transaction(sol_tx(memo="hello"), TREASURY, SPL)[0]["memo_login"] == ""


def test_base58_roundtrip_and_pubkey_validation():
    assert solana_chain.b58encode(solana_chain.b58decode(TREASURY)) == TREASURY
    assert solana_chain.is_pubkey(TREASURY) and not solana_chain.is_pubkey("not-a-key") and not solana_chain.is_pubkey("0OIl")


# ----------------------------------------------------------- bind proof ----

def _keypair():
    sk = Ed25519PrivateKey.generate()
    pub = sk.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return sk, solana_chain.b58encode(pub)


def test_bind_signature_verifies_and_rejects_forgery():
    sk, pub = _keypair()
    sig = base64.b64encode(sk.sign(b"bind me")).decode()
    assert solana_chain.verify_bind_signature(pub, "bind me", sig)
    assert not solana_chain.verify_bind_signature(pub, "bind someone else", sig)
    _, other_pub = _keypair()
    assert not solana_chain.verify_bind_signature(other_pub, "bind me", sig)


# ------------------------------------------------------------- pipeline ----

def _fetch_tx(*txs):
    by_sig = {t["transaction"]["signatures"][0]: t for t in txs}
    def fake(network, treasury, signature, spl=None):
        t = by_sig.get(signature)
        return solana_chain.parse_transaction(t, treasury, spl) if t else []
    return fake


def test_bound_sender_is_credited_at_usd_anchor(monkeypatch):
    monkeypatch.setattr(solana_chain, "fetch_tx", _fetch_tx(sol_tx()))
    deposits.remember_sender(SENDER, ME)
    res = deposits.credit_signature(ME, SIG_SOL)
    # 2 SOL * $100 * 1.01 * 1000
    assert res["credited"] == 1 and res["credited_points"] == 202_000
    assert ledger.balance(ME) == 202_000
    h = ledger.history(ME)[0]
    assert (h["kind"], h["chain"], h["txhash"], h["meta"]["slot"]) == ("deposit", "SOLANA", SIG_SOL, 123)


def test_usdc_is_credited_at_par(monkeypatch):
    monkeypatch.setattr(solana_chain, "fetch_tx", _fetch_tx(usdc_tx()))
    deposits.remember_sender(SENDER, ME)
    assert deposits.credit_signature(ME, SIG_USDC)["credited_points"] == 25_250      # 25 * 1.01 * 1000


def test_memo_attributes_without_a_bind(monkeypatch):
    monkeypatch.setattr(solana_chain, "fetch_tx", _fetch_tx(sol_tx(memo=f"maneki:{ME}")))
    assert deposits.credit_signature(ME, SIG_SOL)["credited"] == 1
    assert deposits.credit_signature(OTHER, SIG_SOL)["credited"] == 0         # a memo names exactly one login


def test_other_users_cannot_claim_a_bound_senders_deposit(monkeypatch):
    monkeypatch.setattr(solana_chain, "fetch_tx", _fetch_tx(sol_tx()))
    deposits.remember_sender(SENDER, ME)
    res = deposits.credit_signature(OTHER, SIG_SOL)
    assert res["credited"] == 0 and res["mine"] is False and ledger.balance(OTHER) == 0


def test_duplicate_signature_never_double_credits(monkeypatch):
    monkeypatch.setattr(solana_chain, "fetch_tx", _fetch_tx(sol_tx()))
    deposits.remember_sender(SENDER, ME)
    deposits.credit_signature(ME, SIG_SOL)
    assert deposits.credit_signature(ME, SIG_SOL)["credited"] == 0
    assert ledger.balance(ME) == 202_000
    with pytest.raises(ledger.DuplicateDeposit):       # schema-level guard, bypassing the pre-check
        ledger.credit(ME, 5, kind="deposit", txhash=SIG_SOL)
    assert ledger.balance(ME) == 202_000               # the failed insert rolled the balance back too


def test_no_price_means_pending_not_credited(monkeypatch):
    monkeypatch.delenv("SOL_USD_OVERRIDE")
    monkeypatch.setattr(pricing, "_SOURCES", ())
    monkeypatch.setattr(pricing, "_cache", {"price": 0.0, "at": 0.0})
    monkeypatch.setattr(solana_chain, "fetch_tx", _fetch_tx(sol_tx()))
    deposits.remember_sender(SENDER, ME)
    res = deposits.credit_signature(ME, SIG_SOL)
    assert res["credited"] == 0 and res["pending"] is True
    assert not ledger.seen_txhash(SIG_SOL)             # left unseen -> retried on the next scan


def test_unfinalized_receipt_is_pending(monkeypatch):
    monkeypatch.setattr(solana_chain, "fetch_tx", _fetch_tx(sol_tx()))
    monkeypatch.setattr(solana_chain, "tx_succeeded", lambda *a, **k: False)
    deposits.remember_sender(SENDER, ME)
    res = deposits.credit_signature(ME, SIG_SOL)
    assert res["credited"] == 0 and res["pending"] is True


def test_dust_is_recorded_as_zero_row(monkeypatch):
    monkeypatch.setattr(solana_chain, "fetch_tx", _fetch_tx(sol_tx(lamports=1)))
    deposits.remember_sender(SENDER, ME)
    assert deposits.credit_signature(ME, SIG_SOL)["credited"] == 0
    assert ledger.seen_txhash(SIG_SOL) and ledger.balance(ME) == 0


# -------------------------------------------------- unattributed parking ----

def test_sweep_parks_unattributable_and_operator_attributes(monkeypatch):
    rows = solana_chain.parse_transaction(sol_tx(), TREASURY, SPL)
    monkeypatch.setattr(solana_chain, "fetch_incoming", lambda *a, **k: rows)
    monkeypatch.setattr(solana_chain, "fetch_tx", _fetch_tx(sol_tx()))
    assert deposits.scan_once()["credited"] == 0 and ledger.balance(ME) == 0
    parked = deposits.list_unattributed()
    assert [p["txhash"] for p in parked] == [SIG_SOL]
    res = deposits.attribute_unattributed(SIG_SOL, ME)
    assert res["credited_points"] == 202_000 and deposits.list_unattributed() == []
    assert deposits.attribute_unattributed(SIG_SOL, ME)["already"] is True      # idempotent
    assert len(deposits.list_unattributed(include_resolved=True)) == 1          # never deleted


def test_sweep_credits_bound_sender_without_the_user_present(monkeypatch):
    rows = solana_chain.parse_transaction(sol_tx(), TREASURY, SPL)
    monkeypatch.setattr(solana_chain, "fetch_incoming", lambda *a, **k: rows)
    deposits.remember_sender(SENDER, ME)
    assert deposits.scan_once()["credited"] == 1 and ledger.balance(ME) == 202_000


def test_one_lamport_poisoning_is_not_parked(monkeypatch):
    rows = solana_chain.parse_transaction(sol_tx(lamports=1), TREASURY, SPL)
    monkeypatch.setattr(solana_chain, "fetch_incoming", lambda *a, **k: rows)
    deposits.scan_once()
    assert deposits.list_unattributed() == []


def test_attribute_rejects_unknown_login(monkeypatch):
    rows = solana_chain.parse_transaction(sol_tx(), TREASURY, SPL)
    monkeypatch.setattr(solana_chain, "fetch_incoming", lambda *a, **k: rows)
    deposits.scan_once()
    with pytest.raises(ValueError):
        deposits.attribute_unattributed(SIG_SOL, "0x" + "9" * 40)


# --------------------------------------------------------------- ledger ----

def test_charge_floors_at_zero_and_never_creates_debt():
    ledger.credit(ME, 50, kind="grant")
    assert ledger.charge(ME, 88, "spend", agent_id="a1") == 0.0
    assert ledger.history(ME)[0]["meta"]["shortfall"] == 38
    n = len(ledger.history(ME))
    assert ledger.charge(ME, 88, "spend") == 0.0 and len(ledger.history(ME)) == n   # nothing written at zero


def test_try_debit_refuses_when_short():
    ledger.credit(ME, 10, kind="grant")
    assert ledger.try_debit(ME, 18) is False and ledger.balance(ME) == 10


def test_credits_are_whole_numbers():
    ledger.credit(ME, 10.9, kind="grant")
    assert ledger.balance(ME) == 10


# ------------------------------------------------------------------ API ----

def test_api_flow_login_bind_challenge_and_guardrails():
    c = TestClient(app, raise_server_exceptions=False)
    assert c.get("/api/fuel").status_code == 401
    tok = c.post("/api/login", json={"address": ME}).json()["token"]
    h = {"Authorization": f"Bearer {tok}"}
    assert c.post("/api/solana/build", headers=h, json={"sender_pubkey": SENDER, "token": "SOL", "amount": 1}).status_code == 409
    sk, pub = _keypair()
    ch = c.post("/api/solana/bind-challenge", headers=h, json={"pubkey": pub}).json()
    assert ch["bound"] is False and pub in ch["message"] and ME in ch["message"]
    bad = c.post("/api/solana/bind", headers=h, json={"pubkey": pub, "signature": base64.b64encode(b"x" * 64).decode()})
    assert bad.status_code == 400 and not deposits.is_bound(pub, ME)            # unproven claims never write
    sig = base64.b64encode(sk.sign(ch["message"].encode())).decode()
    assert c.post("/api/solana/bind", headers=h, json={"pubkey": pub, "signature": sig}).json()["bound"] is True
    assert deposits.is_bound(pub, ME)
    assert c.post("/api/solana/bind", headers=h, json={"pubkey": pub, "signature": sig}).status_code == 400   # one-time
    assert c.post("/api/solana/confirm", headers=h, json={"signature": "abc"}).status_code == 400


def test_api_disabled_without_treasury(monkeypatch):
    monkeypatch.delenv("SOLANA_TREASURY_ADDRESS")
    c = TestClient(app, raise_server_exceptions=False)
    assert c.get("/api/config").json()["enabled"] is False
    tok = c.post("/api/login", json={"address": ME}).json()["token"]
    r = c.post("/api/solana/bind-challenge", headers={"Authorization": f"Bearer {tok}"}, json={"pubkey": SENDER})
    assert r.status_code == 503


def test_api_burn_consumes_fuel():
    ledger.credit(ME, 100, kind="grant")
    c = TestClient(app)
    tok = c.post("/api/login", json={"address": ME}).json()["token"]
    h = {"Authorization": f"Bearer {tok}"}
    assert c.post("/api/demo/burn", headers=h, json={"tier": "claude"}).json()["balance"] == 12
    assert c.post("/api/demo/burn", headers=h, json={"tier": "claude"}).status_code == 402


def test_build_leg_serialises_a_real_transaction(monkeypatch):
    pytest.importorskip("solders")
    monkeypatch.setattr(solana_chain, "_call", lambda n, m, p: {"value": {"blockhash": "11111111111111111111111111111111", "lastValidBlockHeight": 9}})
    out = solana_chain.deposit_build("mainnet", SENDER, TREASURY, 0.5, None, f"maneki:{ME}")
    assert out["token"] == "SOL" and out["tx_b64"] and out["message_b58"]
    from solders.transaction import Transaction
    tx = Transaction.from_bytes(base64.b64decode(out["tx_b64"]))
    assert len(tx.message.instructions) == 2            # transfer + memo
