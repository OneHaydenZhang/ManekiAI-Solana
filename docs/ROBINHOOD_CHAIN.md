# Robinhood Chain deposits — native ETH and USDG

Added **2026-10-10** (after the Solana lane), in production on <https://manekiai.io>. It is the sixth deposit chain and uses the same pipeline as every other chain: registry → provider → append-only Gas ledger → operator publish.

> **Scope note.** This repository is the Solana-focused slice. The Robinhood Chain provider runs in production but is not part of this repo's code; this page documents the design and what was verified.

## What it accepts

| Asset | How it is credited |
|---|---|
| **Native ETH** (Robinhood Chain has no token of its own; gas is ETH) | **By transaction hash.** A plain ETH send emits no log, so no address scan can see it. The wallet returns the hash; the page posts it to `POST /api/points/evm/confirm`; the server verifies the receipt succeeded, the top-level `to` is the treasury, `value > 0`, and the sender is the logged-in wallet. |
| **USDG** (the chain's canonical stablecoin, 6 decimals, `0x5fc5…d168`) | `eth_getLogs` over the registered contract only, recipient-filtered, in 200k-block chunks — and also by hash (the receipt's logs are parsed), so a fresh deposit credits immediately. Credited at par ($1). |

Conversion is the same as everywhere: `gas = floor(amount × price_usd × 1.01 × 1000)`. ETH is priced from the Hyperliquid perpetual mid with a $50–$100,000 sanity window; with no trustworthy price the deposit stays *pending*.

### Why not USDC or USDT

We checked, and as of 2026-10-10 neither has an official deployment on Robinhood Chain: Circle's contract table has no entry for chain 4663, Robinhood's contract page lists only WETH and USDG, and the only "USDT" we found is an 18-decimal look-alike with a 1.56-billion supply (real USDT has 6 decimals). Accepting it would be unsafe. When a canonical USDC or USDT is deployed, the operator publishes its address in the admin console — no code change. Users holding USDC can bridge to USDG (e.g. with Across) and deposit that.

## Verified on the real chain (official RPC, 2026-10-10)

- `eth_chainId = 0x1237` (4663); blocks every ~80 ms; Ethereum finality ~19 minutes behind the head.
- `eth_getLogs` with an address + recipient filter: ~0.5 s up to 200k blocks, ~30 s at 1M — hence the chunking. A default refresh looks back ~1 hour; the operator's manual rescan ~24 hours.
- `USDG.symbol() = "USDG"`, `decimals() = 6`.
- The by-hash path was run against real mainnet ETH transfers (sender, amount and block parsed correctly; a wrong treasury returns nothing) and against real USDG transfers (both the scan and the receipt parse).
- Rollout was two-step: code first with the lane hidden (production health, agents resumed, existing chains unchanged), then the treasury was configured to switch it on.

## Operating notes

- The deposit address reuses the operator's existing EVM treasury — one EVM key works on every EVM chain; an explicit operator choice, never an implicit fallback. It is an EOA on Robinhood Chain (no contract code), so funds are recoverable with the same key.
- Attribution is by **sender**, like all EVM lanes: the paying wallet must be the logged-in wallet, so no bind step is needed (the page refuses to send from a different account).
- The public RPC is rate-limited and "not for production" per Robinhood; operators should set `ROBINHOOD_RPC_URLS` to a keyed gateway.

## Honest limits

1. **Top-level transfers only.** ETH forwarded by a contract (an internal transfer) is not recognised; users send straight from their wallet.
2. **Sequencer confirmation.** Credit follows the sequencer's receipt (sub-second), the same "latest" semantics as the other EVM lanes. Reversal would require Robinhood's sequencer to re-order after posting to Ethereum; a finality threshold for large deposits is future work.
3. **Not yet observed with a real user deposit** at the time of writing.
