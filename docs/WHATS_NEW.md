# What's new for the Solana hackathon

**Reference date:** work counted as *new* starts on **2026-09-12**. Everything before that date is listed separately so judges can tell what existed.

## 1. Already existed before 2026-09-12

| Area | State before the hackathon |
|---|---|
| Maneki platform | Multi-tenant wallet login, autonomous AI trading agents on Hyperliquid, admin console |
| Gas | Per-wallet balance + append-only ledger, USD-anchored pricing (1 USD = 1,000 Gas, +1% top-up bonus), signup bonus |
| Gas consumption | Agents burn Gas per completed decision cycle, priced by model tier; zero balance stops the agent |
| Deposit chains | Injective (INJ), 0G, HyperEVM (HYPE / USDT0 / USDC), X Layer — shared registry → scanner → ledger pipeline |

## 2. New since 2026-09-12 — shipped to production

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

### Treasury sweep — 2026-10-11

| What is new | Why it matters |
|---|---|
| **Robinhood Chain → Arbitrum One sweep console** in the operator admin: ETH → ETH and USDG → native USDC through Across (≈0.04–0.07% fee, 1–2 s) | Collected deposits can be consolidated on Arbitrum |
| **Plan on the server, sign in the operator's wallet** — the platform never holds a private key | A server compromise cannot drain user deposits; same unsigned-transaction pattern as the Solana flow |
| Allowlisted bridge contracts, exact-request re-checks, fee cap, exact-amount approvals, gas reserve, audit trail | Defence in depth before anything is signed |

Details: [TREASURY_SWEEP.md](TREASURY_SWEEP.md).

### Gas economy and onboarding (also after 2026-09-12)

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

## 4. Verification status (honest)

Verified on 2026-09-30, before the production release:

- Mainnet mint decimals re-checked for USDC and USDT (`getAccountInfo`).
- Scanner run against the real treasury; historical USDT deposits parsed correctly (sender, slot, amount).
- Server-built transactions (SOL + memo; USDT `transferChecked` + ATA creation + memo) passed mainnet `simulateTransaction` with no error.
- Front-end flow exercised against a strict DOM harness with the real bundle.
- This repository: 25 tests pass; the transaction builder was exercised against Solana devnet RPC.

**Not yet verified at the time of writing:** a devnet end-to-end deposit (public faucets rate-limited our IPs). Check the treasury on [Solscan](https://solscan.io/account/3FHqTpYb3g8MWpwGfspTyBd3Twfj2X7pKawBg9T8QQSo) for live deposit activity.

Robinhood Chain (2026-10-10): scanned and by-hash parsing verified against the real chain and in production health checks; a real user deposit has not been observed yet.

Treasury sweep (2026-10-11): planning verified against real quotes (also from the production server) and the browser logic end to end against mocks; a real-funds sweep has not been run yet.
