# What's new for the hackathon

**Reference date:** Colosseum's Crypto World's Fair runs from **06:00 PT on 2026-09-14 to 23:59 PT on 2026-10-12** (official rules, §5). Work counted as *new* starts at that moment; everything before that date is listed separately so judges can tell what existed. Every dated item below is a production release date — the earliest is 2026-09-27, so no claim depends on the days just before the start.

## 1. Already existed before 2026-09-14

| Area | State before the hackathon |
|---|---|
| Maneki platform | Multi-tenant wallet login, autonomous AI trading agents on Hyperliquid, admin console |
| Gas | Per-wallet balance + append-only ledger, USD-anchored pricing (1 USD = 1,000 Gas, +1% top-up bonus), signup bonus |
| Gas consumption | Agents burn Gas per completed decision cycle, priced by model tier; zero balance stops the agent |
| Deposit chains | Injective (INJ), 0G, HyperEVM (HYPE / USDT0 / USDC), X Layer — shared registry → scanner → ledger pipeline |
| Cross-chain funding | Settings → *Fund Hyperliquid (cross-chain)*: swap USDC/USDT (Ethereum, BNB Chain, OP Mainnet) or 0G into USDC on Arbitrum through LI.FI, then Hyperliquid's official bridge — built in July 2026 |
| Models | Owners pick a model per agent (OpenRouter and the 0G Compute Router); see [TECHNOLOGIES.md](TECHNOLOGIES.md) |

## 2. New since 2026-09-14 — shipped to production

### Solana deposit lane — 2026-09-30 (the hackathon feature)

| # | What is new | Why it matters |
|---|---|---|
| 1 | **SOL, USDC and USDT deposits → Gas**, the first non-EVM, non-Cosmos chain in the registry | Solana users can fund agents directly |
| 2 | **Server-built unsigned transaction, wallet signs and broadcasts raw bytes**; server confirms by signature | Zero front-end dependencies (no web3.js), no key custody, works with Wallet Standard wallets and legacy `window.solana` |
| 3 | **Wallet bind by signed message** (ed25519 proof of control, one-time, free, 5-minute single-use challenge) | The paying Solana key is unrelated to the EVM login key — this is how a deposit finds its owner without trusting the client |
| 4 | **On-chain attribution memo** `maneki:<login>` attached to every deposit | Second, independent attribution path; also readable on any explorer |
| 5 | **SPL token-account scanner** using pre/post balance deltas, registered mints only | Robust to CPI, batching and ATA-creation transactions; fake airdropped tokens never credit |
| 6 | **Finalized-only crediting** (`meta.err == null` and `finalized`) with signature-level idempotency | No credit on uncertainty; concurrent scans cannot double-credit |
| 7 | **Unattributed-deposit queue + operator attribution** (re-verifies the transaction on-chain, credits through the normal path, never deletes) | Exchange withdrawals cannot be bound; money is never silently lost |
| 8 | **Dust floor** for parking (≥ $0.01) | Found on the live treasury: 1-lamport "address poisoning" sends |
| 9 | **RPC resilience for public gateways**: batch → spaced single calls on HTTP 429, second public gateway, parsed-transaction LRU, negative cache for pruned history | Measured live: the Foundation endpoint returns 429 on a 10-call batch |
| 10 | **Blockhash-expiry auto-rebuild** in the front end | Users who linger in the wallet popup do not fail |
| 11 | **Sanity-windowed SOL pricing** with pending semantics | A broken price feed can never mint Gas |
| 12 | **Operator console card**: treasury publish/validate, token-account state (ATA exists?), keyed-RPC hint | Operators see readiness at a glance |
| 13 | **Promo tiers work on the Solana lane** | Top-up incentives apply to Solana deposits too |

### Robinhood Chain lane — 2026-10-10

| What is new | Why it matters |
|---|---|
| **Native ETH and USDG deposits** on Robinhood Chain (Arbitrum Orbit L2, chain 4663), the sixth deposit chain | Another way to fund agents, built on the same pipeline; details in [ROBINHOOD_CHAIN.md](ROBINHOOD_CHAIN.md) |
| **Credit by transaction hash** for native ETH (a plain send leaves no log to scan), with a resumable pending list in the browser | Native sends are credited without a block explorer or a paid indexer |
| **Generic by-hash confirm endpoint** for EVM chains | Reusable for any future EVM chain whose native transfers matter |
| Safety stance on tokens: **USDC/USDT are not accepted on this chain** because no official deployment exists (a look-alike USDT was found and rejected) | We only credit assets we can verify |

### Cross-chain funding from Robinhood Chain — 2026-10-11

| What is new | Why it matters |
|---|---|
| **Robinhood Chain added as a source** to the existing Settings → Fund Hyperliquid (cross-chain) flow: ETH or USDG → USDC on Arbitrum through LI.FI, then into the user's Hyperliquid account (the flow itself pre-dates the hackathon; the new source chain, USDG pinning and the ETH gas-sizing fix are new) | Users holding assets on Robinhood Chain can fund their agents' trading account without leaving the app; funds stay in the user's own wallet at every step |
| Official-USDG pinning, wallet network auto-add, approval handling, small-amount fee notice | Safety and clarity for a new chain |
| "Get a little gas (≈3)" now sizes ETH sources by USD price | The old behaviour would have sent 3 ETH |

Details: [ROBINHOOD_CHAIN.md](ROBINHOOD_CHAIN.md).

### Gas economy and onboarding (also after 2026-09-14)

| Date | Change |
|---|---|
| 2026-09-27 | **Token-denominated top-up promo tiers with per-tier caps** (priced in the chain's own token rather than USD), alongside a 0G-first model lineup |
| 2026-10-02 | **Wallet-login hardening**: smart-contract wallets are refused up front with a clear message; a stuck wallet request no longer triggers retries; one row per wallet in the picker — fewer failed logins on the way to the Gas panel |

## 3. New in this repository (built for the hackathon)

| Item | Notes |
|---|---|
| Standalone, runnable extraction of the deposit → Gas path | `maneki_solana/` — no dependency on the closed agent runtime |
| `solana_chain.py` | Same module that runs in production |
| `ledger.py`, `deposits.py`, `pricing.py` | Compact, self-contained re-implementations of the production Gas ledger and crediting pipeline (same gates and invariants) |
| Demo web page | `web/index.html` — wallet connect, bind, deposit, ledger, and a "burn Gas" button that stands in for agent cycles |
| 25 tests | Parsing, bind proof, attribution, idempotency, pending-on-no-price, queue, ledger floor-at-zero, API guardrails, and a real transaction-serialization check |
| Documentation | This folder |

## 4. Verification status (honest, as of 2026-10-11)

**Solana deposit lane — real funds, in production.** Three mainnet SOL deposits from three different user accounts have been bound, signed, confirmed and credited by the production lane (2026-09-30, 2026-10-05, 2026-10-11), each with a ledger row carrying the Solana signature and slot. Before release the lane was also checked with mainnet `simulateTransaction` (SOL + memo; USDT `transferChecked` + ATA creation + memo), a read-only scan of the real treasury, and mint-decimals checks. Not done: a devnet end-to-end deposit (public faucets rate-limited our IPs). Treasury activity is public on [Solscan](https://solscan.io/account/3FHqTpYb3g8MWpwGfspTyBd3Twfj2X7pKawBg9T8QQSo).

**Cross-chain funding from Robinhood Chain — real funds, end to end (USDG).** On 2026-10-11 two real USDG → Arbitrum USDC swaps (15 USDG and 6 USDG, exact-amount approval first) completed through the production page (LI.FI → Across route). The first was followed by a real Hyperliquid deposit of 10 USDC through the official bridge, credited to the user's Hyperliquid account (balance 0 → 10), then a perp→spot-dex transfer; the second was followed by another 10 USDC deposit. Not yet run with real funds: the ETH source route.

**Robinhood Chain direct deposit lane (ETH / USDG to the treasury).** Verified against the real chain (log scan and by-hash parsing on real transfers) and in production health checks. **No user deposit through this lane has been credited yet.**

**This repository.** 25 tests pass; the transaction builder was exercised against Solana devnet RPC.
