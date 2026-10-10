# Maneki AI · Solana Deposits & Gas

> **Top up your AI agents with SOL, USDC or USDT.**
> This repository is the open-source slice of [Maneki AI](https://manekiai.io) built for the **Solana hackathon**: a production Solana deposit lane that turns on-chain payments into **Gas**, the unit our trading agents burn to run.

| | |
|---|---|
| **Live product** | <https://manekiai.io> — log in, open the Gas panel, pick **Solana** |
| **Deposit address (mainnet)** | [`3FHqTpYb3g8MWpwGfspTyBd3Twfj2X7pKawBg9T8QQSo`](https://solscan.io/account/3FHqTpYb3g8MWpwGfspTyBd3Twfj2X7pKawBg9T8QQSo) |
| **Accepted** | native **SOL**, **USDC**, **USDT** (SPL, canonical mints only) |
| **Judges start here** | [`docs/JUDGE_GUIDE.md`](docs/JUDGE_GUIDE.md) |
| **Build window** | everything marked *new* below was built **after 2026-09-12** |

## What Maneki is — and what is new

**Maneki already existed before this hackathon.** It is a platform where users launch autonomous AI trading agents on Hyperliquid. Agents run on **Gas**: every completed decision cycle burns Gas, and when the balance hits zero the agent stops. Before 2026-09-12, Gas could be bought with INJ, 0G, HYPE (HyperEVM) and X Layer deposits.

**New for this hackathon: Solana.** Since 2026-09-12 we added:

- **Solana deposits (SOL / USDC / USDT) → Gas** — the fifth deposit chain and the first that is neither EVM nor Cosmos. Shipped to production on 2026-09-30.
- **Gas as the agent's running gas, paid in Solana** — a Solana payment now directly keeps agents running.
- A proof-of-wallet **bind** flow, memo-based attribution, a finalized-only scanner, an unattributed-deposit queue, and RPC resilience for public Solana gateways.
- **Robinhood Chain** (native ETH + USDG) as the sixth deposit chain, shipped 2026-10-10 — see [`docs/ROBINHOOD_CHAIN.md`](docs/ROBINHOOD_CHAIN.md).
- **Fund Hyperliquid from Robinhood Chain** (2026-10-11): Settings → cross-chain funding now accepts ETH or USDG on Robinhood Chain and swaps it to USDC on Arbitrum in the user's own wallet — see [`docs/ROBINHOOD_CHAIN.md`](docs/ROBINHOOD_CHAIN.md).
- **Treasury sweep to Arbitrum One** (2026-10-11): collected deposits move to Arbitrum through Across, planned on the server and signed in the operator's wallet — the platform still never holds a key. See [`docs/TREASURY_SWEEP.md`](docs/TREASURY_SWEEP.md).

See [`docs/WHATS_NEW.md`](docs/WHATS_NEW.md) for the complete list with dates, and what was pre-existing versus new.

## How a Solana deposit works

```
 Browser (Phantom / Solflare / Backpack / OKX — Wallet Standard)
   │ 1. connect wallet
   │ 2. sign a free message once  ──►  POST /api/solana/bind        (ed25519 proof of control)
   │ 3. choose token + amount     ──►  POST /api/solana/build       (server builds the UNSIGNED tx + memo "maneki:<login>")
   │ 4. wallet signs & broadcasts raw bytes  (no web3.js, no private key ever touches our server)
   │ 5. send signature            ──►  POST /api/solana/confirm     (server looks up THAT signature)
   ▼
 Server gates, in order:  mine? → unseen? → priced? → finalized?  → credit Gas (append-only ledger, dedup by signature)
```

Why it is more than "watch an address":

- **Two unrelated keys.** Users log in with an EVM wallet, but pay from a Solana key. The Solana wallet is attached to the login by a one-time **signed message** (free, no transaction); every deposit also carries an on-chain **memo** naming the login. Unproven claims can never write a mapping.
- **Balance deltas, not instruction parsing.** SOL deposits are read from the treasury's lamport delta, SPL deposits from per-mint token-account deltas — robust to CPI, batching and ATA-creation transactions.
- **Only registered mints, only finalized, only error-free.** Fake tokens airdropped to the treasury are ignored. A price feed outage leaves a deposit *pending*, never credited at a guess.
- **Money is never lost.** Deposits that cannot be attributed (e.g. an exchange withdrawal) are parked in a queue for an operator to resolve — never guessed, never deleted.
- **Built for public RPCs.** Batch→single-call degradation on HTTP 429, second gateway, parsed-transaction LRU, negative cache for pruned history, and a dust floor against 1-lamport "address poisoning".

Details: [`docs/SOLANA_DEPOSIT.md`](docs/SOLANA_DEPOSIT.md) · Gas economics: [`docs/GAS.md`](docs/GAS.md).

## Run it locally

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

export SOLANA_TREASURY_ADDRESS=<your base58 treasury>   # unset = Solana lane switched off
export SOLANA_NETWORK=mainnet                             # or: devnet
uvicorn maneki_solana.server:app --port 4190              # open http://localhost:4190
python -m pytest tests -q                                 # 25 tests, no network needed
```

The local page (`web/index.html`) is a compact version of the production top-up flow: pick a demo login, connect a Solana wallet, bind, deposit, watch Gas arrive in the ledger, and press a button to simulate agent cycles burning Gas. For a faucet-funded test use `SOLANA_NETWORK=devnet` (SOL and Circle's devnet USDC are supported).

## Repository layout

```
maneki_solana/
  solana_chain.py   JSON-RPC scanner, tx parser, unsigned-tx builder, ed25519 bind-proof verifier  (as in production)
  deposits.py       the four-gate crediting pipeline, attribution, unattributed queue
  ledger.py         Gas balance + append-only ledger on SQLite (WAL), signature-level dedup
  pricing.py        USD-anchored conversion, sanity-windowed SOL price, burn-rate table
  server.py         FastAPI endpoints + operator endpoints
  config.py         environment-driven configuration
web/index.html     wallet-standard top-up demo page (no build step, no dependencies)
tests/             25 tests over fixed jsonParsed fixtures
docs/              WHATS_NEW · SOLANA_DEPOSIT · ROBINHOOD_CHAIN · TREASURY_SWEEP · GAS · JUDGE_GUIDE · SCOPE
```

## Scope

This repository intentionally contains **only** the deposit → Gas path. The agent runtime is proprietary and is not published; see [`docs/SCOPE.md`](docs/SCOPE.md) for exactly what is and is not in here.

## License

MIT — see [`LICENSE`](LICENSE).
