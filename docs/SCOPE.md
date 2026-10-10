# Scope of this repository

This is a **deliberately partial** open-source release of Maneki AI.

## Included

- Solana deposit lane: wallet discovery, wallet bind, unsigned-transaction builder, scanner, parser, crediting gates, unattributed queue.
- Gas: pricing, balance, append-only ledger, the charge/debit primitives, public burn-rate card.
- A runnable demo (API + page) and tests.
- Documentation of the other deposit lanes added during the hackathon window (e.g. [Robinhood Chain](ROBINHOOD_CHAIN.md)); their provider code is not part of this Solana-focused repository.

## Not included (proprietary)

Maneki's agent technology is our competitive core and is **not published**. This repository does not contain, and the documentation does not describe, the agent runtime. The only touch-point is the interface the runtime uses to spend Gas (`ledger.charge()` / `ledger.try_debit()`), which is exercised here by a demo endpoint.

Also excluded: the rest of the web application, the admin console beyond the endpoints needed for deposits, and all deployment configuration, credentials and operational data.

## About the code you see

- `maneki_solana/solana_chain.py` is the module that runs in production.
- `ledger.py`, `deposits.py`, `pricing.py` and `server.py` are compact, self-contained versions of the production deposit and Gas code, keeping the same gates and invariants but without the platform dependencies.
- The demo login is a stand-in; production verifies a signed wallet message.

## Security

No secrets are stored in this repository. The treasury address is public by design (it is where deposits go). The service only receives funds and holds no signing key. If you find a vulnerability, please open a private security advisory on the repository.
