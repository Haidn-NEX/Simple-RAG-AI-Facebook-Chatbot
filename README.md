# Messenger Sales Chatbot (n8n replacement)

A Messenger chatbot backend that automatically advises customers and closes orders for a shop. It uses a Google Sheet as the content store (prompt, products, scripts), Google Gemini to generate replies, and runs on a private VPS behind Cloudflare Tunnel. Multiple Facebook Pages can be served from one server.

## Status

| Feature | Status |
|---|---|
| Receive and reply to Messenger messages | Running, app in Live mode |
| Multiple Pages | Running |
| Knowledge base from Google Sheet | Running, syncs every 5 minutes; small datasets are put into the prompt in full |
| Send product images by product code | Running |
| Gemini with fallback models and per-model timeouts | Running |
| Order creation, shop owner notifications, scenarios that need actions beyond replying (`K004`, `K006`, `K008`, `K012`, `K013`) | **Not done yet**, top priority |

## Structure

```text
├── AGENTS.md                  # Main instructions for AI coding assistants
├── CLAUDE.md                  # Points Claude Code to AGENTS.md
├── bot/                       # Python + FastAPI source code
│   ├── main.py                # Webhook, Page detection
│   ├── handler.py             # Event dispatch, human-agent mode, image sending
│   ├── ai.py                  # Gemini calls, fallback models, markdown cleanup
│   ├── kb.py                  # Google Sheet sync and retrieval
│   ├── messenger.py           # Send API with per-Page tokens
│   ├── database.py            # SQLite (users, conversations, orders)
│   ├── config.py              # Configuration from .env
│   └── .env.example           # Environment variable template
├── LICENSE
├── site/                      # Privacy Policy, Terms, Data Deletion
└── docs/
    ├── ARCHITECTURE.md        # Architecture and data flow
    ├── RANG-BUOC-NEN-TANG.md  # Messenger Platform constraints
    ├── VAN-HANH-VPS.md        # SSH, deploy, logs, rollback
    ├── MULTI-PAGE-SETUP.md    # Adding a Page
    └── FAQ-TROUBLESHOOTING.md # Known errors and fixes
```

Documentation under `docs/` is written in Vietnamese.

## Running locally

```bash
cd bot
python -m venv venv && venv/Scripts/activate     # Windows
pip install -r requirements.txt
cp .env.example .env                              # then fill in real values
python main.py
```

Without `SHEET_ID`, the bot falls back to `bot/products.json`. Deploying to a VPS: [docs/VAN-HANH-VPS.md](docs/VAN-HANH-VPS.md).

Never commit `.env`, tokens, or customer data (`bot/data/`); they are excluded by `.gitignore`.

## License

MIT, see [LICENSE](LICENSE).
