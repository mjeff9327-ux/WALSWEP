# Mnemonic Hunter v2.0

A live crypto seed phrase scanner + auto-sweeper with a **Rich TUI**. Generates real BIP39 mnemonics (or bulk manual input), derives addresses via BIP44 across 8 chains, checks live mainnet balances including ERC-20/TRC-20 tokens, and optionally auto-sweeps found funds. Telegram notifications and license key gating are built in.

## Quick Start

```bash
pip install -r requirements.txt
python main.py
```

## Controls

| Key | Action |
|---|---|
| `Space` | Start / Stop scanning |
| `1`-`8` | Toggle BTC / ETH / LTC / SOL / BNB / XRP / TRON / POLYGON |
| `M` | Toggle auto-generate / manual seed input |
| `S` | Toggle auto-sweep on/off |
| `E` | Export found wallets to `exports/` |
| `L` | Load seeds from `seeds.txt` |
| `Q` | Quit |

## Configuration

Edit `config.json` to enable:

- **Telegram alerts** — set `telegram.bot_token` and `chat_id`
- **Auto-sweep** — set `sweep.destination_wallet` per chain, enable `sweep.auto_broadcast`
- **License key** — set `license.telegram_bot_token` and `enabled`
- **Affiliate split** — set `affiliate.enabled`, `dev_split`, `affiliate_split`
- **API server** — set `api.auto_start_server: true`

## Supported Chains & APIs

| Chain | API | Balance |
|---|---|---|
| BTC | blockstream.info | Native BTC |
| ETH | etherscan.io | Native ETH + ERC-20 USDT |
| LTC | blockcypher.com | Native LTC |
| SOL | solana RPC | Native SOL |
| BNB | bscscan.com | Native BNB |
| XRP | xrpscan.com | Native XRP |
| TRON | trongrid.io | Native TRX + TRC-20 USDT |
| POLYGON | polygonscan.com | Native MATIC |

## How It Works

1. **BIP39 Mnemonic** — generated randomly or loaded from `seeds.txt`
2. **BIP44 Derivation** — addresses for all 8 chains via `bip-utils`
3. **Balance Check** — live mainnet API queries per chain
4. **Token Scan** — ERC-20 USDT (ETH) + TRC-20 USDT (TRON)
5. **Auto-Sweep** — if balance > `min_balance_usd`, signs and optionally broadcasts transfer to `destination_wallet`
6. **Notifications** — Telegram alerts on every FOUND event
7. **Export** — found wallets + keys to `exports/` directory

## Architecture

```
app/
├── api/                  FastAPI gateway
├── components/           MempoolMonitor, FundingDetector, AutoSweepBot, ConfigManager
├── implementations/      Bip39Solver, LiveNodeClient, TelegramNotifier, etc.
├── interfaces/           Abstract contracts (INodeClient, ISolver, IKeyStore, etc.)
├── storage/              SQLite
└── tui/                  Rich Live TUI + SeedInputPanel + ExportHandler
config.json               Settings
events/                   Webhook recordings
exports/                  Exported wallets
seeds.txt                 Bulk seed input
```

## Dependencies

`rich`, `mnemonic`, `bip-utils`, `httpx`, `fastapi`, `uvicorn`, `pydantic`
