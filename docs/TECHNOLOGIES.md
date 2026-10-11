# Technologies, integrations and AI tools

What Maneki is built with, what the hackathon work added, which AI models run inside the product, and which AI tools were used to build it. The agent runtime itself stays private (see [SCOPE.md](SCOPE.md)); this page describes it only at the level of "what it is built from".

## 1. Stack

| Layer | Technology |
|---|---|
| Backend | Python 3.12, FastAPI + Uvicorn, SQLite (WAL) |
| Front end | Plain JavaScript, no build step and no wallet library (Wallet Standard events + EIP-1193 `window.ethereum`) |
| Solana | JSON-RPC over `httpx` (`getSignaturesForAddress`, `getTransaction` jsonParsed, `getTokenAccountsByOwner`, `getSignatureStatuses`), `solders` to build unsigned transactions, Ed25519 verification with `cryptography` to prove wallet control, SPL Token `TransferChecked`, SPL Memo |
| EVM | JSON-RPC (`eth_getLogs`, receipts) for 0G, HyperEVM, X Layer and Robinhood Chain; `eth_account` for operator-side signing only |
| Trading venue | Hyperliquid (official Python SDK) |
| Cross-chain | **LI.FI** (user funding: Robinhood Chain ETH/USDG → USDC on Arbitrum → Hyperliquid's official bridge) |
| Price feeds | Hyperliquid perpetual mids (SOL, ETH) with sanity windows; stablecoins at par |

## 2. What the hackathon work added to the stack

- **Solana deposit lane** — server-built unsigned transaction, wallet-signed, confirmed by signature; ed25519 bind + memo attribution; finalized-only crediting. See [SOLANA_DEPOSIT.md](SOLANA_DEPOSIT.md).
- **Robinhood Chain lane** — USDG by log scan, native ETH by transaction hash. See [ROBINHOOD_CHAIN.md](ROBINHOOD_CHAIN.md).
- **Cross-chain funding from Robinhood Chain** — a new source in the existing LI.FI funding flow.

## 3. AI models inside the product

Maneki is **model-agnostic**: the owner chooses the model for each agent, and the model's decisions are bounded by code (see §4). The model families offered today:

| Route | Models |
|---|---|
| **0G Compute Router** (decentralized, TEE-capable inference; offered to new agents) | Claude Sonnet 5, Claude Opus 4.7, GPT-5.5, GPT-5.4, GLM 5.3 / 5.2, DeepSeek V3 / V4 Flash, Qwen 3.7 Max / Flash, MiniMax M3, Gemini 2.5 Flash, and 0G's own `0gm-1.0-35b-a3b` |
| **OpenRouter** (kept for existing agents) | Claude (Sonnet 4.5 default, Haiku 4.5), GPT-4o-mini, Gemini 2.5 Flash, DeepSeek Chat, MiniMax-01 |

Gas is priced by model tier (the public price card in [GAS.md](GAS.md)), so the model chosen directly determines how fast Gas is burned. Every model id is verified against its router before it is offered; a model without tool-calling support is excluded.

## 4. Agent orchestration (high level)

- **No third-party agent framework.** The agent loop is purpose-built Python: a model is called through the OpenAI-compatible chat API (the `openai` SDK) on OpenRouter or the 0G Router, and through the Anthropic Messages format for Claude models on 0G.
- The model proposes; **code decides what is allowed.** Orders go to Hyperliquid through the official SDK only after code-enforced checks, and every completed cycle charges Gas through the same ledger described in [GAS.md](GAS.md).
- Details beyond this (prompts, memory, evaluation, risk logic) are intentionally not published.

## 5. AI tools used to build it

- **Claude Code** (Anthropic's CLI coding agent) was used throughout the hackathon work for implementation, tests, documentation, review and deployment scripting. The Claude model used is recorded in each commit's `Co-Authored-By` trailer — across the project's history during the contest window these are **Claude Fable 5 / 5.1, Claude Opus 5 and Claude Sonnet 5.5**; the commits in this repository carry Sonnet 5.5.
- **Human direction and verification.** Product decisions, scope, and what may be published were set by the team. Nothing was marked done without running it: unit tests, mainnet `simulateTransaction` and read-only checks against real chains, live quotes, and — where funds were available — real transactions (see the verification status in [WHATS_NEW.md](WHATS_NEW.md)).

## 6. Third-party code and services

- **Open-source dependencies** are listed in `requirements.txt` (FastAPI, Uvicorn, httpx, cryptography, solders) and used under their own licenses; no third-party code is vendored into this repository. This repository's own code is MIT-licensed.
- **Third-party services** used through public APIs only: Solana JSON-RPC gateways, the Hyperliquid API, LI.FI, the 0G Compute Router and OpenRouter.
- The contest-window work is original to the team; AI assistance is described in section 5.

