# Mnemonic Hunter v2.0

A live crypto seed/key **search, recovery, and auto-sweep engine** with a **Textual TUI**. Scans filesystem vaults, hardware wallets, brain wallets, and MPC shares for recoverable seeds — then derives addresses, checks live mainnet balances, and auto-sweeps found funds to configurable destination wallets.

## Quick Start

```bash
pip install -r requirements.txt
python main.py
```

## Controls

| Key / Button | Action |
|---|---|
| `Space` | Start / Stop random mnemonic scanning |
| `1`-`8` | Toggle BTC / ETH / LTC / SOL / BNB / XRP / TRON / POLYGON |
| `DERIVE` | Open Derivation panel — enter seed phrase, derive address + private key |
| `SWEEP` | Open Sweep Orchestrator — 12 operations (see below) |
| `E` | Export found wallets to `exports/` |
| `L` | Load seeds from `seeds.txt` |
| `Q` | Quit |

## Sweep Orchestrator Operations

| Button | What It Does |
|--------|-------------|
| **Scan Filesystem for Wallets** | Finds wallet files (MetaMask, Exodus, Phantom, Trust Wallet, Electrum, etc.) by name + content scan (embedded mnemonics/hex keys/WIF) |
| **Brain Wallet Dictionary Scan** | SHA256(passphrase) → BIP39 → derive all 8 chain addresses → check live balances → auto-sweep |
| **Crack Encrypted Vaults** | Dictionary attack on detected vaults (PBKDF2-SHA256/SHA512, Scrypt, NaCl) → extract mnemonic → check balances → auto-sweep |
| **Scan for MPC Share Files** | Find Shamir/SSS/SLIP-0039 share files → reconstruct secret over secp256k1 prime → convert to BIP39 → check balances → auto-sweep |
| **Detect Hardware Wallet** | Detect Trezor/OneKey (trezorlib) + Ledger (ledgerblue) → derive per-chain BIP44 addresses → check live balances → auto-sweep |
| **MPC Threshold Signing** | Demonstrates 2-of-3 Shamir's Secret Sharing scheme |
| **Sweep — BTC/ETH/LTC/SOL/BNB** | Enter a mnemonic → derive → check balance → auto-sweep to destination |
| **Sweep — All Wallets** | Sweep all 8 chains from a single mnemonic |

## Configuration

Edit `config.json` to enable:

- **Auto-sweep** — `sweep.auto_broadcast` defaults to `true`. Set `destination_wallet` per chain.
- **Telegram alerts** — set `telegram.bot_token` and `chat_id`
- **License key** — set `license.secret_key` for HMAC-based validation
- **Affiliate split** — set `affiliate.enabled`, `dev_split`, `affiliate_split`, `dev_wallet`, `affiliate_wallet`
- **Cracker wordlist** — set `cracker.password_list`; `max_attempts` defaults to 4,000,000
- **Brainwallet dictionary** — set `brainwallet.dictionary_path`; `max_phrases` defaults to 4,000,000
- **API keys** — set `api_keys.etherscan`, `bscscan`, `polygonscan`, `coingecko`
- **Performance** — per wallet `performance` section: `max_workers` (0=auto), `balance_cache_ttl`, `enable_hashcat`, `enable_rainbow_cache`, `smart_password_rules`

## Search/Recovery Coverage

| Wallet Category | Coverage |
|----------------|----------|
| **Hardware wallets** (cold storage) | Trezor, Trezor Model T, OneKey (via trezorlib); Ledger Nano S/X (via ledgerblue+ledgereth) |
| **Multi-chain software wallets** | MetaMask, Exodus, Phantom, Trust Wallet — vault detection + password cracking |
| **Seedless/MPC wallets** | Shamir's Secret Sharing share files (.json, .share, .sss, SLIP-0039) — reconstruction over secp256k1 prime |
| **Brain wallets** | Any passphrase → SHA256 → BIP39 entropy → BIP44 derivation |
| **Filesystem keys** | Content scan for embedded 12-24 word BIP39 mnemonics, 0x-prefixed hex private keys, WIF keys |

## Supported Chains & APIs

| Chain | API | Transaction Signing |
|-------|-----|-------------------|
| BTC | blockstream.info | UTXO secp256k1 (coincurve) with per-input script signing |
| ETH | etherscan.io | EIP-155 RLP (coincurve + keccak) |
| LTC | blockcypher.com | UTXO secp256k1 (coincurve) with per-input script signing |
| SOL | solana RPC | ed25519 canonical message format (PyNaCl) |
| BNB | bscscan.com | EIP-155 RLP (coincurve + keccak) |
| XRP | xrpscan.com | Ed25519 canonical XRP binary blob |
| TRON | trongrid.io | secp256k1 + TronGrid broadcast |
| POLYGON | polygonscan.com | EIP-155 RLP (coincurve + keccak) |

## Transaction Signing Fixes (v2.0)

| Chain | Fix Applied |
|-------|------------|
| **XRP** | Replaced `str(dict)` non-deterministic signing with canonical XRP binary serialization (field-ID + length + value format) |
| **SOL** | Replaced `bytes.fromhex()` (base58 decode error) with proper base58 + canonical Solana message format (MessageHeader + account keys + blockhash + instruction) |
| **BTC/LTC** | Replaced `bytes.replace()` script injection (corrupts multi-input txs) with full transaction rebuild per signed input |

## How It Works

1. **Source** — Random BIP39 generation, bulk `seeds.txt`, filesystem vault scan (name + content), brainwallet dictionary, HW wallet USB detection, MPC share file discovery
2. **BIP44 Derivation** — addresses for all 8 chains via `bip-utils`
3. **Balance Check** — live mainnet API queries per chain
4. **Token Scan** — ERC-20 USDT (ETH) + TRC-20 USDT (TRON)
5. **Auto-Sweep** — if balance > `min_balance_usd`, TransactionSigner builds real raw transaction (EVM RLP, UTXO with per-input scripts, ed25519 canonical, XRP binary blob), signs, and broadcasts to `destination_wallet`
6. **Notifications** — Telegram alerts on every FOUND event
7. **Export** — found wallets + keys to `exports/` directory

## Performance Features (v2.0)

| Feature | Description |
|---------|-------------|
| **Multiprocessing cracking** | ProcessPoolExecutor parallelizes vault decryption across CPU cores |
| **Hashcat GPU acceleration** | Auto-detects hashcat; falls back to CPU multiprocessing if unavailable |
| **Smart password rules** | Generates case variants + common suffix mutations from each dictionary entry |
| **Multiprocessing brainwallet scan** | ProcessPoolExecutor scales dictionary scanning across all cores |
| **mmap file loading** | Memory-maps password/dictionary files for 2-5x faster reads |
| **Rainbow cache (SQLite)** | Pre-computes SHA256→BIP39 entropy; subsequent runs skip derivation |
| **Batch RPC balance checks** | Etherscan/BSCScan/Polygonscan use `balancemulti` (20/batch), Solana uses `getMultipleAccounts` (100/batch) |
| **Concurrent balance sweeps** | `asyncio.gather` checks all chains in parallel during sweep |
| **Combined regex alternation** | Single O(1) pattern replaces N sequential regex matches in vault scanning |
| **BIP32 cache** | Single-chain derivation nodes cached; Bip44.FromSeed results cached per (purpose, coin) |

## Dependencies

`textual`, `rich`, `mnemonic`, `bip-utils`, `httpx`, `fastapi`, `uvicorn`, `pydantic`, `coincurve`, `pynacl`, `pycryptodome`, `rlp`, `base58`, `trezor`, `ledgereth`, `ledgerblue`, `hwi`
