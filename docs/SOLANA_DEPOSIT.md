# Solana deposit lane — technical design

Maneki credits **Gas** for SOL, USDC and USDT sent to a single treasury address. This document explains the design decisions; the code is in `maneki_solana/`.

## 1. Constraints that shaped the design

1. **Identity mismatch.** Maneki users log in with an EVM wallet (Hyperliquid compatibility). A Solana payer is a different ed25519 key with no relation to that login. We must attribute a payment to a login without trusting the client.
2. **Token accounts.** SPL balances live in per-mint token accounts owned by the treasury, not at the treasury address.
3. **Cost model.** On EVM one `getLogs` returns everything; on Solana each transaction needs its own `getTransaction`. Public RPC endpoints rate-limit hard.
4. **No custody, no web3.js.** The server never holds a user key and the front end has no third-party dependency.

## 2. Flow

| Step | Actor | Endpoint / action |
|---|---|---|
| 1 | Browser | Discover wallets through Wallet Standard events (`wallet-standard:app-ready` / `register-wallet`); fall back to `window.phantom.solana`, `solflare`, `backpack`, `okxwallet.solana`, `window.solana`. |
| 2 | Browser → Server | `POST /api/solana/bind-challenge {pubkey}` returns a single-use message (login, pubkey, nonce, timestamp; 5-minute TTL). |
| 3 | Wallet | `signMessage` — free, no transaction. |
| 4 | Browser → Server | `POST /api/solana/bind {pubkey, signature}` → server verifies **ed25519** and stores `sol:<pubkey> → login`. |
| 5 | Browser → Server | `POST /api/solana/build {sender_pubkey, token, amount}` → server checks the wallet is bound, prices the deposit, runs a best-effort balance pre-flight, and returns an **unsigned transaction** (`tx_b64` for Wallet Standard, `message_b58` for legacy Phantom). |
| 6 | Wallet | Signs and broadcasts raw bytes (`solana:signAndSendTransaction`). If the blockhash expired while the user waited, the front end rebuilds once. |
| 7 | Browser → Server | `POST /api/solana/confirm {signature}` → server fetches *that* transaction and runs the gates (§4). Pending results are retried by the page and by the Refresh button. |

### The transaction we build

```
[ create treasury ATA (idempotent) ]   only for SPL, only if the treasury ATA is missing
SystemProgram.transfer                  native SOL
   or SPL Token TransferChecked         tag 12, u64 amount LE, u8 decimals
Memo  "maneki:<0x login>"               attribution, visible on any explorer
```

Fee payer is the user. Operators pre-create the USDC/USDT ATAs so users never pay that rent. `solders` is imported lazily: if it is missing, only *building* is unavailable — the service still boots and scans.

## 3. Attribution (three tiers)

| Priority | Evidence | Who can credit |
|---|---|---|
| 1 | `deposit_senders["sol:<pubkey>"]` — written **only** after a verified ed25519 bind | the user's refresh, the background sweep |
| 2 | Memo `maneki:<0x>` naming a registered login | the user's refresh, the background sweep |
| 3 | neither | **nothing is credited**; the row goes to `deposit_unattributed` for an operator |

Security properties:

- **No bind hijack.** Attaching someone else's Solana key to your login requires a valid signature from that key; the challenge embeds the login and expires in five minutes, single use. A later valid bind overrides an earlier one (whoever can prove the private key wins).
- **Memo cannot steal.** A memo can only direct money *to* the login it names, so naming someone else's login only donates to them.
- **Case-sensitive keys.** Base58 is case-sensitive; Solana sender keys are stored verbatim under a `sol:` namespace and never lower-cased.
- **No phantom users.** A base58 key is never interpreted as a login.

## 4. Crediting gates

Every entry point — `credit_signature` (post-send), `scan_for_user` (Refresh), `scan_once` (operator sweep) — runs the same four gates:

1. **mine?** the sender is attributable to this login (§3); the sweep only credits senders attributable beyond doubt and parks the rest;
2. **unseen?** the signature is not in the ledger (pre-check **and** a partial UNIQUE index on deposit signatures, so concurrent scans cannot double-credit);
3. **priced?** a trustworthy USD price exists — otherwise the deposit stays *pending* and untouched;
4. **finalized?** `meta.err == null` and commitment `finalized`. Any RPC trouble returns *false*: the scan retries, we never credit on uncertainty.

We only ever read **incoming transfers** to the treasury. A treasury *balance* is never read, so refreshing cannot hand out a lump sum.

## 5. Scanner

- **Watched accounts** = treasury address + every registered mint's token accounts, resolved via `getTokenAccountsByOwner` (cached 10 minutes). RPC-resolved rather than derived: no curve math, and non-ATA accounts are covered.
- **Signatures** come from `getSignaturesForAddress(account, finalized)`, merged and de-duplicated; failed ones are dropped immediately. The ledger's `seen` check skips already-credited signatures *before* paying for `getTransaction`.
- **Parsing** uses `jsonParsed` transactions and **balance deltas**:
  - SOL: `postBalances[i] − preBalances[i]` for the treasury key; sender = the `system.transfer` source whose destination is the treasury, falling back to the fee payer.
  - SPL: per-mint delta of token accounts **owned by the treasury**; sender = the owner of the account that lost the most of that mint.
  - Only **registered mints** count. One transaction with several incoming mints yields one row per `(signature, token)`.
- **Rows** look like `{txhash, sender_sol, sender_keys:["sol:<pubkey>"], memo_login, amount, token, block(slot), ts}`.

### Public-RPC resilience (measured, not theoretical)

| Observation (live, 2026-09-30) | Mitigation |
|---|---|
| Foundation endpoint answers **429** to a 10-call batch | Batches of 5; on any batch failure fall back to spaced (0.25 s) single calls with one retry |
| A second public gateway does not retain old history (returns `null`) | Ordered gateway list; `null` results go to a 10-minute **negative cache** so scans do not re-pay for them |
| Repeated user refreshes | LRU of parsed transactions (2,000); a signature in the cache already proves "finalized and error-free", so the receipt call is skipped |
| Production load | Operators should set `SOLANA_RPC_URLS` to a keyed gateway; the operator console flags when they have not |

A signature that cannot be fetched is simply left for the next scan: nothing is credited without its details and nothing is lost, because dedup is by signature.

## 6. Failure modes

| Risk | Handling |
|---|---|
| Fake or airdropped tokens sent to the treasury | Only registered mints are credited; deltas are filtered per mint |
| Failed / unfinalized transaction | `meta.err == null` **and** `finalized`; errors in verification return *false* |
| Same signature returned for several watched accounts | Signature-set de-duplication + ledger unique index |
| Blockhash expiry (~60–90 s) | Build immediately before signing; one automatic rebuild |
| Treasury ATA missing | Created idempotently inside the user's transaction (user pays ≈0.002 SOL); operators pre-create to avoid it |
| User short on SOL for fees | Best-effort pre-flight (`amount + 0.005 SOL`). **Unreadable ≠ zero:** if the balance lookup fails the request is allowed through |
| Dust (< 1 Gas) | Recorded as a zero-point ledger row so the signature enters the dedup index |
| Address poisoning (1-lamport sends) | Not parked in the operator queue below $0.01 (never credited either) |
| Exchange withdrawals | Cannot be bound or memo'd → parked; operator attributes after on-chain re-verification |
| Treasury private key | The service **only receives** — it holds no key |

## 7. Devnet

`SOLANA_NETWORK=devnet` switches the cluster, the explorer suffix and the mint set: only SOL and Circle's devnet USDC exist there (USDT is dropped). The service never watches a mint that cannot exist on the cluster it scans.

## 8. Operator endpoints

| Endpoint | Purpose |
|---|---|
| `GET /admin/unattributed` | list parked deposits |
| `POST /admin/attribute {txhash, address}` | re-verify on-chain, credit the named login, mark resolved (idempotent, never deletes) |

Both require the `X-Admin-Token` header matching `ADMIN_TOKEN`; without that variable the endpoints are closed.
