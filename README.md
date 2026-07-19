<div align="center">

# 🤖 ClawLite

### Your personal AI assistant. Runs on your machine. Your data stays yours.

[![License](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.11+-green.svg)](https://python.org)
[![Telegram](https://img.shields.io/badge/interface-Telegram-blue.svg)](https://telegram.org)
[![Local First](https://img.shields.io/badge/local--first-yes-brightgreen.svg)]()
[![Built by ForgeSynapse](https://img.shields.io/badge/built%20by-ForgeSynapse%20LTD-orange.svg)](https://forgesynapse.com)
[![Download](https://img.shields.io/badge/download-Windows%20installer-success.svg)](https://github.com/forgesynapseltd/ClawLite/releases/latest)

**[Getting Started](#getting-started) · [Features](#features) · [Architecture](#architecture) · [Contributing](#contributing)**

</div>

---

## What is ClawLite?

ClawLite is a personal AI assistant that lives in Telegram and runs entirely on your machine.

No subscription. No cloud. No data leaving your device without your explicit permission.

You ask it something — it thinks, searches the web if needed, remembers the context, and answers. Like having a Jarvis, but one that actually respects your privacy.

```
You:        What happened in the Champions League today?
ClawLite:   [searches web] PSG beat Bayern 5-4 in a thriller at Parc des Princes...

You:        Remind me tomorrow to review the match highlights.
ClawLite:   Got it. I'll remind you tomorrow about the match highlights.

You:        What's my name?
ClawLite:   Fernando. You told me earlier today.
```

---

## Features

**🔒 Local-First by Design**
Your conversations never leave your machine. ClawLite runs on Ollama — a local LLM that operates with zero outbound traffic. If Ollama is unavailable, it can optionally fall back to Groq cloud, but it tells you explicitly when that happens.

**🔍 Real-Time Web Search**
Powered by Tavily, ClawLite searches the web in real time when it needs current information. Not a cached index — the actual web, right now.

**🧠 Persistent Memory**
ClawLite remembers context across conversations using a local SQLite database with semantic search. It recalls relevant past exchanges without storing anything in the cloud.

**🛡️ Sandbox Protection**
Every tool call and HTTP request is validated against an allowlist before execution. ClawLite cannot access your filesystem, execute arbitrary code, or connect to unauthorized domains.

**☁️ Transparent Cloud Fallback**
If Ollama is unavailable and you have a Groq API key configured, ClawLite uses cloud inference — and always tells you when it does. No silent data transfers.

**📰 Daily Brief**
Enable an optional morning summary: ClawLite searches for relevant news and delivers a personalized brief at the time you choose, every day.

---

## Getting Started

### Windows: one-click installer (recommended)

No terminal, no admin rights, no technical knowledge required.

1. Download the installer from the [latest release](https://github.com/forgesynapseltd/ClawLite/releases/latest).
2. Run `ClawLite-Setup-X.Y.Z.exe` and follow the wizard.
3. A setup page opens in your browser asking for your Telegram bot token and Tavily API key — get a bot token from `@BotFather` on Telegram (`/newbot`) and a free key from [tavily.com](https://tavily.com), then paste them in.

That's it — ClawLite starts automatically once setup is complete.

### Manual install (any OS, developers)

#### Prerequisites

- Python 3.11+
- [Ollama](https://ollama.com) installed and running
- A Telegram account
- A [Tavily](https://tavily.com) API key (free tier is enough)

#### 1. Get your Telegram bot token

Open Telegram, find `@BotFather`, and run `/newbot`. Copy the token it gives you.

#### 2. Clone and configure

```bash
git clone https://github.com/forgesynapseltd/ClawLite.git
cd ClawLite
cp .env.example .env
```

Edit `.env` with your credentials:

```bash
TELEGRAM_BOT_TOKEN=your_token_here
TAVILY_API_KEY=your_tavily_key_here
```

#### 3. Pull the local model

```bash
ollama pull llama3.2
```

#### 4. Install and run

```bash
pip install -r requirements.txt
python -m clawlite.main
```

Or with Docker:

```bash
docker compose up -d
```

Open Telegram, find your bot, and send `/start`. That's it.

---

## Architecture

ClawLite is built in layers, each with a single responsibility:

```
Telegram message
      ↓
Sandbox validation      ← blocks malicious content before anything runs
      ↓
Planner                 ← decides: search web / recall memory / answer directly
      ↓
Tool execution          ← Tavily search or memory recall
      ↓
LLM (Ollama local)      ← generates the response
      ↓
Memory store            ← saves the exchange to local SQLite
      ↓
Telegram response
```

**Stack:**
- `python-telegram-bot` — Telegram interface
- `ollama` — local LLM inference (llama3.2 by default)
- `groq` — optional cloud fallback
- `tavily-python` — real-time web search
- `onnxruntime` — local embeddings for semantic memory (no PyTorch)
- `sqlalchemy` + SQLite — persistent local storage
- `loguru` — structured logging

---

## Commands

| Command | Description |
|---|---|
| `/start` | Introduction and onboarding |
| `/help` | List of available commands |
| `/status` | Show current LLM, search, and sandbox status |
| `/memory clear` | Delete all your conversation history |
| `/brief on` | Enable daily morning brief |
| `/brief off` | Disable daily morning brief |

---

## Configuration

All configuration lives in `.env`. Key settings:

| Variable | Default | Description |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | — | Required. Get from @BotFather |
| `TAVILY_API_KEY` | — | Required. Get from tavily.com |
| `OLLAMA_MODEL` | `llama3.2` | Local model to use |
| `GROQ_API_KEY` | — | Optional. Cloud fallback |
| `SANDBOX_MODE` | `strict` | `strict` or `permissive` |
| `BRIEF_ENABLED` | `false` | Enable daily brief |
| `BRIEF_HOUR` | `8` | Hour for daily brief (local time) |

---

## Why ClawLite?

The AI assistant market has a problem nobody talks about openly.

Every "personal" assistant is personal in name only. Your queries leave your device. Your context is stored on someone else's server. Your usage patterns are being analyzed. And every month, you pay for the privilege.

ClawLite is the alternative. Built on the belief that a truly personal assistant should run on your hardware, search the real web on your behalf, remember your context without that context leaving your machine, and be installable by anyone — not just developers.

No subscription. No cloud dependency. No compromises on what "personal" means.

---

## Roadmap

- [x] Telegram bot with local LLM
- [x] Real-time web search (Tavily)
- [x] Persistent local memory with semantic recall
- [x] Sandbox protection
- [x] Cloud fallback with transparency
- [ ] Daily brief scheduler
- [ ] Voice input (Whisper)
- [ ] WhatsApp support
- [ ] Plugin/skill marketplace
- [ ] One-click desktop installer

---

## Contributing

ClawLite is fully open source under Apache 2.0. Contributions are welcome.

1. Fork the repository
2. Create a feature branch: `git checkout -b feature/your-feature`
3. Read [CLA.md](CLA.md) — by submitting a PR you accept it
4. Submit your pull request

For major changes, open an issue first to discuss what you'd like to change.

---

## License

Apache 2.0 — see [LICENSE](LICENSE) for details.

ClawLite is a project by [ForgeSynapse LTD](https://forgesynapse.com) (Company No. 16692140, England & Wales).

---

<div align="center">

*"Your data. Your machine. Your assistant."*

**[⭐ Star this repo](https://github.com/forgesynapse/clawlite)** if ClawLite is useful to you. It helps more people find it.

</div>
