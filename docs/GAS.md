# Gas — how a Solana payment becomes agent run time

**Gas** is Maneki's unit of agent run time — the panel in the app is titled "⚡ Agent Gas" (燃料 in the Chinese interface). It is **not** the blockchain network fee: that is paid separately, in SOL (or ETH on Robinhood Chain), when you send a deposit.

## 1. Buying Gas

```
gas = floor( amount × price_usd × 1.01 × 1000 )
```

- **1 USD = 1,000 Gas**, plus a **1% top-up bonus** (the `1.01`).
- **USDC / USDT** are credited at par ($1).
- **SOL** is priced from the Hyperliquid perpetual mid (falling back to a second public source in this repo), cached for 60 s, with a last-good fallback for up to one hour, and a sanity window ($5–$5,000). Outside the window, or with no price at all, the deposit stays **pending** and is retried — Gas is never minted from a guessed price.
- Gas is always a **whole number**; remainders are dropped, never rounded up.
- The price actually used is recorded on the ledger row, so every credit is auditable.

| Example (SOL = $150) | Gas |
|---|---|
| 0.10 SOL | floor(0.10 × 150 × 1.01 × 1000) = **15,150** |
| 25 USDC | **25,250** |

## 2. The ledger

Append-only, one row per movement, each row snapshotting `balance_after`:

| Field | Meaning |
|---|---|
| `kind` | `deposit`, `grant`, `spend`, … |
| `points` | signed Gas delta |
| `token`, `token_amount`, `price_usd`, `usd_value` | what was paid and at what price |
| `txhash` | the Solana signature (deposits) |
| `chain`, `meta` | `SOLANA`, plus the slot and block time |

Invariants:

- **Idempotent by signature** — a pre-check and a partial UNIQUE index (`kind='deposit'`). If the index fires, the balance update rolls back together with the ledger insert.
- **Rows are never edited or deleted.** Corrections are new rows.
- **A charge never creates debt.** If the balance is already zero, nothing is written; if it is lower than the charge, it is charged down to zero and the shortfall is recorded in `meta`.
- **Dust** (< 1 Gas) is stored as a zero-point row so it is never rescanned.

## 3. Spending Gas (the agent side)

An agent run is a sequence of decision cycles. After each completed cycle the platform charges Gas, by model tier — this is the public price card:

| Tier | Gas per decision cycle |
|---|---|
| Chat message | 8 |
| Standard models | 18 |
| GPT-class models | 68 |
| Claude-class models | 88 |

When the balance reaches zero the agent stops, and the owner gets a notice to top up. That is the loop Solana now feeds: **pay with Solana → Gas → agents keep running.**

In this repository the consumption side is represented by `ledger.charge()` / `ledger.try_debit()` and a demo endpoint, `POST /api/demo/burn`, wired to the "Simulate an agent cycle" buttons on the local page. The agent runtime that calls them in production is not published (see [SCOPE.md](SCOPE.md)).

## 4. Why Solana fits

- Gas purchases are small and frequent; Solana's fees keep a $5 top-up economical.
- Stablecoin top-ups (USDC / USDT) make the cost of running an agent predictable.
- One-time wallet bind plus a memo means a repeat user simply pays; no per-deposit paperwork.
