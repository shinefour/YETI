# YETI

Your Everyday Task Intelligence — a personal AI-centric productivity system that consolidates Teams, Slack, Jira, Notion, Calendar, and Email into one intelligent hub.

## Quick Start

```bash
# Install
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"

# Configure (see Credentials below)
cp .env.example .env
# Edit .env with your keys

# Run
.venv/bin/uvicorn yeti.app:app --reload
```

Then open `http://localhost:8000/dashboard`.

## Interfaces

| Interface | Access |
|-----------|--------|
| Web dashboard | `http://localhost:8000/dashboard` |
| CLI | `yeti chat`, `yeti status`, `yeti actions`, `yeti add-action "title"` |
| Telegram | Message `@YetiSystemBot` (or your bot) |
| API | `http://localhost:8000/api/` |

## Browser Authentication

Every non-public route (anything except `/health` and `/webhooks/*`) requires the dashboard API key. To sign a browser in for 30 days:

1. **Find the key.**
   - Local: `grep YETI_DASHBOARD_API_KEY .env`
   - Production: `grep YETI_DASHBOARD_API_KEY .kamal/secrets`
2. **Visit the login URL once** with the key in the query string:
   ```
   https://yeti.diconve.com/?key=<YETI_DASHBOARD_API_KEY>
   ```
   (Local dev: `http://localhost:8000/?key=<KEY>`)
3. The server sets a `yeti_session` cookie (httponly, secure, samesite=strict, 30-day TTL) and redirects to `/dashboard`. Subsequent requests are auto-authenticated until the cookie expires.

**Sign out:** clear the `yeti_session` cookie in browser dev tools (Application → Cookies → delete).

**Rotate the key:** change `YETI_DASHBOARD_API_KEY` in secrets, run `kamal deploy`. All existing cookies invalidate; re-auth via step 2.

**Headless / CLI / scripts:** send `x-api-key: <KEY>` header instead — no cookie needed.

```bash
curl -H "x-api-key: $YETI_DASHBOARD_API_KEY" https://yeti.diconve.com/api/inbox/count
```

## Credentials

Copy `.env.example` to `.env` and fill in the values below.

### Required

**Anthropic API Key** — powers the Chat Agent via Claude.
- Go to https://console.anthropic.com/settings/keys
- Create a new key
- Set `YETI_ANTHROPIC_API_KEY` in `.env`

**Telegram Bot Token** — mobile interface.
1. Open Telegram and message `@BotFather`
2. Send `/newbot` and follow the prompts
3. Copy the token (format: `123456789:AABBC...`)
4. Set `YETI_TELEGRAM_BOT_TOKEN` in `.env`

**Telegram Chat ID** — restricts the bot to your account only.
1. Send any message to your new bot
2. Open `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates` in a browser
3. Find `"chat":{"id":123456789}` in the response
4. Set `YETI_TELEGRAM_ALLOWED_CHAT_ID` in `.env`

### Optional

**Jira** — issue tracking integration.
- Go to https://id.atlassian.com/manage-profile/security/api-tokens
- Create an API token
- Set in `.env`:
  - `YETI_JIRA_URL` — your Jira instance (e.g. `https://yourcompany.atlassian.net`)
  - `YETI_JIRA_EMAIL` — your Jira account email
  - `YETI_JIRA_API_TOKEN` — the token you created

**Notion** — page and database integration.
- Go to https://www.notion.so/profile/integrations
- Create a new internal integration
- Copy the token
- Set `YETI_NOTION_API_KEY` in `.env`
- Share the Notion pages/databases you want YETI to access with your integration

**OpenAI** — fallback model for the Chat Agent.
- Go to https://platform.openai.com/api-keys
- Create a key
- Set `YETI_OPENAI_API_KEY` in `.env`

**Slack** — messaging integration.
- Go to https://api.slack.com/apps and create a new app
- Add Bot Token Scopes: `channels:history`, `channels:read`, `chat:write`
- Install to your workspace
- Set `YETI_SLACK_BOT_TOKEN` in `.env`

**Microsoft 365** (Teams, Calendar, Email) — requires an Azure AD app registration.
- Go to https://portal.azure.com → Azure Active Directory → App registrations
- Register a new application
- Add API permissions: `Mail.Read`, `Calendars.Read`, `Chat.Read`, `User.Read`
- Create a client secret
- Set in `.env`:
  - `YETI_MICROSOFT_CLIENT_ID`
  - `YETI_MICROSOFT_CLIENT_SECRET`
  - `YETI_MICROSOFT_TENANT_ID`

## Running with Docker

```bash
# Local development with all services
docker compose up

# Just the API + Redis
docker compose up yeti-api redis
```

## Running Tests

```bash
.venv/bin/pytest tests/ -v
```

## Production Deployment

YETI uses [Kamal 2](https://kamal-deploy.org/) for zero-downtime deployment to a Hetzner VPS.

### Prerequisites

**Ruby 3.1+** — Kamal is a Ruby gem.
```bash
brew install ruby
```

Verify the new Ruby is in your path:
```bash
ruby --version  # should be 3.1+
```

If `ruby --version` still shows the system Ruby (2.6), add the Homebrew Ruby to your path:
```bash
echo 'export PATH="/usr/local/opt/ruby/bin:$PATH"' >> ~/.zshrc
source ~/.zshrc
```

**Kamal 2:**
```bash
gem install kamal
kamal version  # should be 2.x
```

If `kamal` is not found after install, use the full path from `gem environment gemdir`:
```bash
$(gem environment gemdir)/bin/kamal version
```

**GitHub Container Registry token:**
- Go to https://github.com/settings/tokens
- Create a classic token with `write:packages` scope
- Save it for the secrets file below

### Configure Secrets

Create `.kamal/secrets` (not committed to git):
```
KAMAL_REGISTRY_USERNAME=<github-username>
KAMAL_REGISTRY_PASSWORD=<github-pat-token>
YETI_ANTHROPIC_API_KEY=<your-key>
YETI_TELEGRAM_BOT_TOKEN=<your-token>
YETI_TELEGRAM_ALLOWED_CHAT_ID=<your-chat-id>
```

### Deploy

```bash
# First-time: provisions server, installs Docker, starts accessories
kamal setup

# Subsequent deploys
kamal deploy

# Rollback if something breaks
kamal rollback

# View logs
kamal app logs
kamal accessory logs redis

# SSH into app container
kamal app exec -i bash
```

### Server Requirements

- Hetzner CPX31 (4 vCPU, 8 GB RAM) or similar
- Ubuntu 24.04
- SSH key access as root
- Firewall: allow ports 22, 80, 443
- Domain with A record pointing to the server IP

See `config/deploy.yml` for the full Kamal configuration and `design.md` for architecture details.
