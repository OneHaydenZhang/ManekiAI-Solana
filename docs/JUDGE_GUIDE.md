# Judge guide — try Maneki and pay with Solana

Two ways to experience the work. Path A is the real product; path B is the local demo in this repository.

> **What to know first.** Maneki is a live platform with AI trading agents (it existed before the hackathon). **What we added for this hackathon is Solana top-up and the Gas it buys**; Gas is what the agents run on. See [WHATS_NEW.md](WHATS_NEW.md).
>
> **Naming.** **Gas** is Maneki's unit of agent run time (panel title "⚡ Agent Gas"; 燃料 in the Chinese interface). It is *not* the blockchain network fee — that is paid separately in SOL/ETH when you send a deposit.

## Path A — the live product (<https://manekiai.io>)

### Prerequisites

- A browser wallet that can **log in with an EVM account** (MetaMask, Rabby, Phantom's EVM account, OKX, …). Smart-contract wallets such as Coinbase Smart Wallet are not supported for login.
- A **Solana wallet** for paying: Phantom, Solflare, Backpack or OKX Wallet. It can be the same extension as above (e.g. Phantom has both account types).
- A few dollars of SOL, USDC or USDT on **Solana mainnet**. Fees are tiny; 0.01 SOL covers many deposits. Use a small amount — this is a real payment.

### Steps

1. **Open** <https://manekiai.io> and choose **Log in / Connect wallet**. Approve the signature request. New wallets receive a small signup bonus of Gas (1,000 at the time of writing), so you can look around before paying.
2. **Open the Gas panel** — "⚡ Agent Gas". It shows your balance, the price card, and the **Top up** button.
3. **Pick the chain: Solana.** The token selector then offers **SOL**, **USDC** and **USDT**. The quote line shows how much Gas you will receive (1 USD = 1,000 Gas + 1% bonus).
4. **Connect your Solana wallet** (the line under the selector). If you have several, choose one from the drop-down.
5. **Sign the bind message (first time only).** Your wallet shows a plain-text message: *"bind Solana wallet … to 0x… · nonce … — no transaction, no fee."* This proves the Solana wallet is yours, so its deposits credit your account. Nothing is sent on-chain.
6. **Enter an amount and press Top up.** A confirmation dialog shows amount, token, chain and Gas to be credited. Confirm.
7. **Approve the transaction in your wallet.** It is a standard transfer to the treasury plus a memo `maneki:<your login>`. If the popup sits too long and the blockhash expires, the page rebuilds the transaction for you.
8. **Watch it land.** The page says *"Sent — waiting for finality…"*, then *"Credited +N Gas"* with a link to the transaction on Solscan, usually within a minute. If it is still pending, press **Refresh** (or reload) — the scan retries by itself.
9. **Check the ledger.** In the Gas panel, **Top-ups & rewards** lists the deposit with its slot and Solscan link; **Spending** lists what agents burned.
10. **Put the Gas to work.** Create an agent from the main screen and start it. Each completed decision cycle burns Gas (Standard 18 · GPT 68 · Claude 88 per cycle) and the per-agent spend is shown on the agent card. Agent creation and live trading are part of the existing product and may require connecting a Hyperliquid account; you do not need to do this to evaluate the Solana part.

### Verify on-chain

- Treasury: [`3FHqTpYb3g8MWpwGfspTyBd3Twfj2X7pKawBg9T8QQSo`](https://solscan.io/account/3FHqTpYb3g8MWpwGfspTyBd3Twfj2X7pKawBg9T8QQSo)
- Your deposit transaction shows the transfer **and** the `maneki:0x…` memo in its instruction list.
- The ledger row's `txhash` is that transaction's signature.

### Also new: Robinhood Chain

In the same Top up card, choose **Robinhood Chain** to pay with native **ETH** or **USDG** from your normal EVM login wallet (the page adds the network for you). The deposit is credited right after the transaction is confirmed. Details: [ROBINHOOD_CHAIN.md](ROBINHOOD_CHAIN.md).

### Also new: fund Hyperliquid from Robinhood Chain

In **Settings → Fund Hyperliquid (cross-chain)**, step 1 now offers **Robinhood Chain** as a source. Choose ETH or USDG, enter an amount, press **Get quote**, then **Swap now**: the page adds the network to your wallet and swaps to USDC on Arbitrum in your own wallet. Step 2 deposits that USDC to Hyperliquid's official bridge. Use a small amount first — it is a real swap.

### Things worth trying (the interesting edges)

| Try this | Expected |
|---|---|
| Deposit **USDC**, then **USDT** | Credited at par; the treasury's token accounts already exist, so you pay no rent |
| Press **Refresh** after a credited deposit | Nothing is credited twice (dedup by signature) |
| Send from a **different, unbound** Solana wallet with no memo (advanced) | Not credited to anyone; it is parked for an operator instead of being guessed |
| Reject the signature popups | Clean "Cancelled" message, no state change |

## Path B — run the demo yourself (5 minutes)

```bash
git clone <this repository> && cd <this repository>
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
export SOLANA_TREASURY_ADDRESS=<a Solana address you control>
export SOLANA_NETWORK=devnet          # free test funds from https://faucet.solana.com
uvicorn maneki_solana.server:app --port 4190
```

1. Open <http://localhost:4190>, press **Log in** (leave the field empty to generate a demo identity).
2. Choose a token and amount, press **Deposit**; connect your wallet (set it to **Devnet**), sign the bind message, approve the transaction.
3. The balance and ledger update; use **Simulate an agent cycle** to burn Gas and see an `402 Out of Gas` when the balance runs dry.
4. Run `python -m pytest tests -q` for the 25 tests (no network needed).

Notes for the demo: login here is a stand-in (the live product verifies a signed wallet message). On devnet only SOL and Circle's devnet USDC exist. If no price feed is reachable from your network, set `SOL_USD_OVERRIDE=150` to fix a price for the demo.

## If something goes wrong

- **No Solana wallet detected** — install Phantom/Solflare/Backpack/OKX Wallet, reload the page, and make sure the extension is enabled for the site.
- **"Not enough SOL"** — the page checks your wallet before the popup; fund it with a little SOL for fees.
- **Pending for more than a few minutes** — press Refresh. Credits require a *finalized* transaction; busy public RPC nodes can delay confirmation.
- **Support reference** — error messages shown in the product carry a short reference id; quote it if you contact us.
