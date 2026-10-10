# Treasury sweep — move collected deposits to Arbitrum without putting a key on the server

Added **2026-10-11**, in production. Deposits on Robinhood Chain (native ETH, USDG) accumulate in the treasury address. Operators want them on Arbitrum One. This page explains how that works **without the platform ever holding a private key**.

## The design rule

> The service only **receives**. It never signs, and no signing key exists on any server.

A fully automatic sweeper would need the treasury key on the server, which means a server compromise would drain every user's deposits. So the sweep is built as **plan on the server, sign in the operator's wallet**:

```
operator opens Admin → Gas → "Robinhood Chain sweep"
   │  GET  /plan                 server reads balances, asks Across for a quote, VALIDATES it,
   │                             returns UNSIGNED transactions (+ the allowlist it used)
   │  browser re-checks the plan (targets on the allowlist) and asks the operator to confirm
   │  wallet signs and sends     only the treasury account is accepted
   │  POST /record               append-only audit entry for every step
   └─ GET  /status               polls Across until the deposit is filled on Arbitrum
```

This is the same pattern as the Solana deposit flow (server builds an unsigned transaction, the wallet signs it), applied to operations.

## Route

| Asset on Robinhood Chain | Arrives on Arbitrum One as | Fee (measured 2026-10-11) | Fill |
|---|---|---|---|
| ETH | ETH | ≈ 3.7 bps (0.05 ETH) | ~1 s |
| USDG | native USDC (1:1) | ≈ 6.7 bps (100 USDG) | ~2 s |

Bridging uses [Across](https://across.to). Other options were checked and rejected: Relay costs about 0.4% for ETH, and the canonical Arbitrum bridge does not apply because Robinhood Chain settles to Ethereum rather than Arbitrum One (a withdrawal takes 7 days and cannot land on Arbitrum One).

## Safety checks (all run before anything reaches a signer)

1. **Contract allowlist.** The quote's target contract and any approval spender must be one of the known Across contracts. A compromised or spoofed quote API cannot redirect funds to another contract.
2. **Exact request.** Chain, input amount, transaction value, destination chain and output token are compared with what was asked for.
3. **Fee cap.** The total fee must stay under 50 bps. Small amounts, where Across' fixed cost dominates (1 USDG ≈ 77 bps), are skipped instead of being swept at a loss.
4. **Exact-amount approvals.** The quote API proposes an unlimited ERC-20 approval; the plan rewrites it to the exact amount.
5. **Gas reserve.** 0.0005 ETH always stays on Robinhood Chain.
6. **Wallet must be the treasury.** The console refuses any other account; the command-line variant refuses a key that does not derive the treasury address.
7. **Redacted errors.** Internal failures are returned as a reference id, never as raw details; the endpoints require the operator token.

The same validated core backs both the console and an operator-side command-line script (dry-run by default; the key is read from the environment of the operator's own shell and never stored).

## Verification (honest)

- Unit tests cover the allowlist, amounts, destination, fee cap, approval rewrite, reserve, hostile quotes, error redaction and authorization.
- The browser logic was run end to end against mocks (load → approve → bridge → audit → filled) and rejects a tampered plan without sending anything.
- Planning was run against real quotes, from the production server too; an ETH route was simulated on-chain from a funded third-party address (no signature, no broadcast).
- **Not yet done:** a real-funds end-to-end sweep, and an on-chain simulation of the USDG route. The first live sweep should be a small ETH amount.
