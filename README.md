# 🛡️ Render SRE Self-Healing & AI Monitor Agent

An autonomous **Keep-Alive, Self-Healing & AI Crash Triage Agent** for Render-hosted web applications.

Designed specifically to avoid the common pitfalls of naive GitHub Actions monitors:
1. **Zero Commit Spam on `main`:** Telemetry (`data/checks.csv`, `data/state.json`) and the live HTML status dashboard (`index.html`) are force-pushed as a **single squashed orphan commit to `gh-pages`**. Your `main` branch stays 100% clean and your repository size never grows.
2. **Zero Render Build Minutes Consumed:** Self-healing uses Render’s container restart API (`POST /v1/services/{id}/restart`), never redeploying from scratch, preserving your 500 free monthly build minutes.
3. **Gemini 3.8 Flash Crash Triage:** On failure, pulls the last ~100 lines from Render’s `/v1/logs` API and sends a 3-line root-cause summary + fix directly to your Telegram (with a deterministic heuristic fallback for 512MB OOM / SIGKILL / gateway timeouts).
4. **No Alert Fatigue:** Retries 3x with exponential backoff (`2s → 4s → 8s`) before declaring `DOWN`, alerts **only on state transitions** (`UP → DOWN`, `DOWN → UP`, or 3 consecutive slow checks `>5s`), and caps auto-restarts at **2 per hour**.

---

## 📁 Repository Structure

```text
RenderSREAgent/
├── .github/
│   └── workflows/
│       └── monitor.yml              # Runs every 10m + restores/saves state on orphan gh-pages
├── health_endpoints/                # Drop-in /health routes for your Render app
│   ├── fastapi_health.py            # FastAPI router (includes 512MB RAM headroom check)
│   ├── flask_health.py              # Flask Blueprint
│   └── node_express_health.js       # Express.js router
├── src/
│   ├── config.py                    # Env/Secret loader & IST (UTC+5:30) helpers
│   ├── checker.py                   # HTTP check, 3x backoff retry, cold-start & SSL expiry
│   ├── state_manager.py             # State machine, incident log & 30-day rolling CSV
│   ├── render_client.py             # Render API v1 log fetcher & container restarter
│   ├── ai_diagnoser.py              # Gemini 3.8 Flash root-cause analyzer + OOM fallback
│   ├── alerter.py                   # Telegram Bot API (primary) + SMTP Email (fallback)
│   ├── dashboard.py                 # Zero-dependency dark-mode Status Page + SVG chart
│   └── main.py                      # Main cycle orchestrator
├── tests/
│   └── test_monitor.py              # Unit tests for state transitions & rate limiting
├── .env.example
├── requirements.txt
└── README.md
```

---

## 🚀 5-Minute Setup Checklist

### Step 1: Add `/health` to Your Render App
Copy the matching file from `health_endpoints/` into your Render application:
- **FastAPI:** Copy `health_endpoints/fastapi_health.py` and register `app.include_router(router)`
- **Flask:** Copy `health_endpoints/flask_health.py` and register `app.register_blueprint(health_bp)`
- **Node/Express:** Copy `health_endpoints/node_express_health.js` and mount `app.use(require('./node_express_health'))`

Verify it returns HTTP 200 with `"status": "ok"`.

---

### Step 2: Push This Folder to a Public GitHub Repo
GitHub Actions minutes are **unlimited and free on public repositories**.
```bash
git init
git branch -M main
git add .
git commit -m "feat: initialize Render SRE Self-Healing & AI Monitor Agent"
git remote add origin https://github.com/<YOUR_USERNAME>/<YOUR_MONITOR_REPO>.git
git push -u origin main
```

---

### Step 3: Add GitHub Repository Secrets
Go to your GitHub repo → **Settings → Secrets and variables → Actions → New repository secret**:

| Secret Name | Required | Where to Get It |
| :--- | :---: | :--- |
| `TARGET_URL` | ✅ | Your full health URL, e.g. `https://your-app.onrender.com/health` |
| `APP_NAME` | Optional | Display name for alerts & dashboard (e.g. `Hallucination Detector API`) |
| `EXPECTED_KEYWORD` | Optional | Keyword expected in `/health` body (default: `ok`) |
| `TELEGRAM_BOT_TOKEN` | ✅ | Message `@BotFather` on Telegram → `/newbot` |
| `TELEGRAM_CHAT_ID` | ✅ | Message `@userinfobot` on Telegram to get your numeric ID |
| `RENDER_API_KEY` | ✅ | Render Dashboard → **Account Settings → API Keys** (`rnd_...`) |
| `RENDER_SERVICE_ID` | ✅ | Render Dashboard → click your service → copy `srv-...` from the URL bar |
| `GEMINI_API_KEY` | ✅ | [Google AI Studio](https://aistudio.google.com/apikey) (Free tier) |
| `SMTP_USER` | Optional | Fallback email sender address (e.g. `you@gmail.com`) |
| `SMTP_PASSWORD` | Optional | Gmail App Password (16-char password from Google Account Security) |
| `ALERT_EMAIL_TO` | Optional | Recipient email for fallback alerts |

---

### Step 4: Enable Live Status Dashboard on GitHub Pages
1. Go to **Actions** tab → click **Render SRE Self-Healing & AI Monitor** → click **Run workflow** once.
2. After it finishes (creates the `gh-pages` branch), go to **Settings → Pages**.
3. Under **Build and deployment**, select **Deploy from a branch** → Branch: `gh-pages` / `/ (root)` → **Save**.
4. Your live status dashboard is now hosted at `https://<YOUR_USERNAME>.github.io/<YOUR_MONITOR_REPO>/`.

---

### Step 5: Set Up the 5-Minute Heartbeat on cron-job.org (Crucial!)
GitHub Actions cron schedules (`*/10 * * * *`) can drift by 15–40 minutes during peak GitHub load, which is longer than Render’s 15-minute sleep timer.
To guarantee **0 cold starts**:
1. Create a free account at [cron-job.org](https://cron-job.org) (or [UptimeRobot](https://uptimerobot.com)).
2. Create a job pointing to `https://your-app.onrender.com/health` every **5 minutes**.
3. *(Optional Quota Saver)*: If you host **multiple** free apps on the same Render account (which share 750 free hours/month), set the cron schedule on cron-job.org to run only during active hours (`08:00–24:00 IST`) to use ~480 hours/month instead of 744.

---

## 🧪 Running Locally & Running Unit Tests

```bash
# Run the full unit test suite
python -m unittest discover -s tests -v

# Run a single live monitor cycle locally (Generates data/checks.csv, data/state.json & public/index.html)
python -m src.main
```
