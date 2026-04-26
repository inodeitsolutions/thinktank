# Virtual Startup Think Tank — Deployment Guide (v2)

CrewAI-based multi-agent "startup council" running in a Parallels VM on your M1 Max. Hybrid setup: local orchestration, Claude API for inference, Tavily for web research, full observability via Langfuse v3 with per-agent token and cost tracking.

**This is the corrected version after an honest-to-goodness debugging session. Every step here has been verified working as of April 2026.**

---

## Architecture

```
┌─────────────────────────── Your M1 Max ───────────────────────────┐
│                                                                    │
│   ┌── Parallels VM: "thinktank" (Debian 12 ARM64) ──────────────┐  │
│   │                                                              │  │
│   │   Docker Compose stack:                                      │  │
│   │                                                              │  │
│   │   ┌──────────────┐   ┌──────────────┐                       │  │
│   │   │ CrewAI app   │   │ Postgres 16  │                       │  │
│   │   │ + FastAPI    │   │ (app + lf)   │                       │  │
│   │   │ + OpenLit    │   └──────────────┘                       │  │
│   │   └──────┬───────┘                                           │  │
│   │          │                                                   │  │
│   │   ┌──────┴───────────────── Langfuse v3 ────────────────┐   │  │
│   │   │ langfuse-web  langfuse-worker                        │   │  │
│   │   │ ClickHouse    Redis    MinIO                         │   │  │
│   │   └──────────────────────────────────────────────────────┘   │  │
│   │                                                              │  │
│   └──────────────────────────────────────────────────────────────┘  │
│                                                                    │
└──────────────┬─────────────────────────────────────────────────────┘
               │
               ├──→ Anthropic API (Opus 4.7 / Sonnet 4.6 / Haiku 4.5)
               └──→ Tavily API (web research)
```

**Agent roster:**

| Agent | Model | Role |
|---|---|---|
| CEO / Orchestrator | Claude Opus 4.7 | Synthesize final brief |
| Product Strategist | Claude Sonnet 4.6 | MVP scope, roadmap |
| CTO / Architect | Claude Sonnet 4.6 | Tech stack, risks |
| Head of Growth | Claude Sonnet 4.6 | Positioning, GTM |
| Competitive Analyst | Claude Haiku 4.5 + Tavily | Market research |
| Devil's Advocate | Claude Opus 4.7 | Red-team everything |

Cost per idea: €0.80–€2.00 in API calls plus 2–4 Tavily credits.

---

## Part 1 — Parallels VM

### Create the VM

1. **Parallels Desktop → File → New** → "Install Windows, Linux, or another OS from an image file"
2. Download Debian 12 ARM64 netinst ISO: `https://cdimage.debian.org/debian-cd/current/arm64/iso-cd/` — filename must contain `arm64`
3. Drag ISO in. When Parallels says "unrecognized OS," pick **Other Linux → Debian**
4. Name: `thinktank`. Check **"Customize settings before installation"**
5. Customize:
   - **CPU & Memory**: 4 vCPU, 8192 MB RAM
   - **Hard Disk**: 80 GB, expanding (bumped from 60GB since Langfuse v3 with ClickHouse needs more headroom)
   - **Network**: Shared Network
   - **Startup and Shutdown → On Mac shutdown**: "Keep running in background"

### Debian install

Minimal install:
- **Hostname:** `thinktank`
- **User:** whatever matches your SSH key (`drmilosz` in my case)
- **Software selection:** UNCHECK desktop/GNOME, CHECK only SSH server + standard utilities
- **GRUB:** install to `/dev/vda`

Reboot. Then install Parallels Tools: **Actions → Install Parallels Tools** from the Parallels menu.

---

## Part 2 — Base system prep

From macOS:

```bash
ssh drmilosz@thinktank.local
```

If `thinktank.local` doesn't resolve, find the IP in Parallels Control Center and add to your Mac's `/etc/hosts`.

```bash
sudo apt update && sudo apt full-upgrade -y

sudo apt install -y curl git ca-certificates gnupg ufw fail2ban \
                    build-essential vim htop

# Docker
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/debian/gpg | \
  sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
sudo chmod a+r /etc/apt/keyrings/docker.gpg
echo "deb [arch=arm64 signed-by=/etc/apt/keyrings/docker.gpg] \
https://download.docker.com/linux/debian bookworm stable" | \
  sudo tee /etc/apt/sources.list.d/docker.list
sudo apt update
sudo apt install -y docker-ce docker-ce-cli containerd.io \
                    docker-buildx-plugin docker-compose-plugin
sudo usermod -aG docker $USER

# Firewall
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow ssh
sudo ufw enable
```

**Log out and back in** so docker group applies, then verify:

```bash
docker run --rm hello-world
```

**Snapshot #1 in Parallels:** name it `base-debian-docker`. Rollback point.

---

## Part 3 — API keys

**Anthropic:** `https://console.anthropic.com` → Billing → add €5+ credit → API Keys → Create Key → copy `sk-ant-...`

**Tavily:** `https://tavily.com` → sign up → API Keys → copy `tvly-...`. Free tier = 1,000 credits/month, plenty to start.

---

## Part 4 — Project layout

```bash
mkdir -p ~/thinktank/app
cd ~/thinktank
```

End state:

```
~/thinktank/
├── .env                    # Secrets
├── docker-compose.yml
├── init.sql
├── app/
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── thinktank.py        # CrewAI agents
│   └── main.py             # FastAPI wrapper
└── data/
    ├── postgres/
    ├── clickhouse/
    ├── clickhouse-logs/
    └── minio/
```

---

## Part 5 — Configuration files

### `.env`

Generate all secrets at once and paste into `~/thinktank/.env`:

```bash
cat <<EOF >> ~/thinktank/.env
# App DB
POSTGRES_PASSWORD=$(openssl rand -base64 24)

# Langfuse v3
LANGFUSE_SALT=$(openssl rand -base64 32)
LANGFUSE_SECRET=$(openssl rand -base64 32)
LANGFUSE_ENCRYPTION_KEY=$(openssl rand -hex 32)
NEXTAUTH_URL=http://localhost:3000

# ClickHouse, Redis, MinIO
CLICKHOUSE_PASSWORD=$(openssl rand -base64 24)
REDIS_AUTH=$(openssl rand -base64 24)
MINIO_ROOT_PASSWORD=$(openssl rand -base64 24)

# Langfuse auto-init (org/project/user/API keys on first boot)
LANGFUSE_INIT_ORG_ID=thinktank-org
LANGFUSE_INIT_ORG_NAME=Thinktank
LANGFUSE_INIT_PROJECT_ID=thinktank-project
LANGFUSE_INIT_PROJECT_NAME=Thinktank
LANGFUSE_INIT_PROJECT_PUBLIC_KEY=pk-lf-thinktank-local
LANGFUSE_INIT_PROJECT_SECRET_KEY=$(openssl rand -hex 32)
LANGFUSE_INIT_USER_EMAIL=you@example.com
LANGFUSE_INIT_USER_NAME=Your Name
LANGFUSE_INIT_USER_PASSWORD=pick-a-real-password

# External APIs — paste your actual keys here
ANTHROPIC_API_KEY=sk-ant-...
TAVILY_API_KEY=tvly-...
EOF

chmod 600 ~/thinktank/.env
```

**Edit the file afterward** to paste your real Anthropic and Tavily keys and set a real user email/password.

### `docker-compose.yml`

```yaml
services:
  postgres:
    image: postgres:16-alpine
    environment:
      POSTGRES_DB: thinktank
      POSTGRES_USER: thinktank
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
    volumes:
      - ./data/postgres:/var/lib/postgresql/data
      - ./init.sql:/docker-entrypoint-initdb.d/init.sql:ro
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U thinktank"]
      interval: 10s
      timeout: 5s
      retries: 5
    restart: unless-stopped

  clickhouse:
    image: clickhouse/clickhouse-server:latest
    environment:
      CLICKHOUSE_DB: default
      CLICKHOUSE_USER: clickhouse
      CLICKHOUSE_PASSWORD: ${CLICKHOUSE_PASSWORD}
    volumes:
      - ./data/clickhouse:/var/lib/clickhouse
      - ./data/clickhouse-logs:/var/log/clickhouse-server
    ulimits:
      nofile:
        soft: 262144
        hard: 262144
    healthcheck:
      test: ["CMD-SHELL", "wget -qO- http://localhost:8123/ping || exit 1"]
      interval: 10s
      timeout: 5s
      retries: 10
    restart: unless-stopped

  redis:
    image: redis:7-alpine
    command: >
      --requirepass ${REDIS_AUTH}
      --maxmemory 256mb
      --maxmemory-policy allkeys-lru
    healthcheck:
      test: ["CMD", "redis-cli", "-a", "${REDIS_AUTH}", "ping"]
      interval: 10s
      timeout: 5s
      retries: 5
    restart: unless-stopped

  minio:
    image: minio/minio:latest
    command: server /data --console-address ":9001"
    environment:
      MINIO_ROOT_USER: minio
      MINIO_ROOT_PASSWORD: ${MINIO_ROOT_PASSWORD}
    volumes:
      - ./data/minio:/data
    ports:
      - "0.0.0.0:9001:9001"
    healthcheck:
      test: ["CMD", "mc", "ready", "local"]
      interval: 10s
      timeout: 5s
      retries: 5
    restart: unless-stopped

  # Creates the MinIO bucket Langfuse needs — MinIO doesn't auto-create buckets
  minio-init:
    image: minio/mc:latest
    depends_on:
      minio:
        condition: service_healthy
    environment:
      MINIO_ROOT_PASSWORD: ${MINIO_ROOT_PASSWORD}
    entrypoint: >
      /bin/sh -c "
      mc alias set local http://minio:9000 minio $$MINIO_ROOT_PASSWORD &&
      mc mb --ignore-existing local/langfuse &&
      echo 'bucket ready';
      "
    restart: "no"

  langfuse-worker:
    image: langfuse/langfuse-worker:3
    depends_on:
      postgres: { condition: service_healthy }
      clickhouse: { condition: service_healthy }
      redis: { condition: service_healthy }
      minio: { condition: service_healthy }
      minio-init: { condition: service_completed_successfully }
    environment: &langfuse-common
      DATABASE_URL: postgresql://thinktank:${POSTGRES_PASSWORD}@postgres:5432/langfuse
      SALT: ${LANGFUSE_SALT}
      ENCRYPTION_KEY: ${LANGFUSE_ENCRYPTION_KEY}
      TELEMETRY_ENABLED: "false"

      CLICKHOUSE_URL: http://clickhouse:8123
      CLICKHOUSE_MIGRATION_URL: clickhouse://clickhouse:9000
      CLICKHOUSE_USER: clickhouse
      CLICKHOUSE_PASSWORD: ${CLICKHOUSE_PASSWORD}
      CLICKHOUSE_CLUSTER_ENABLED: "false"

      REDIS_HOST: redis
      REDIS_PORT: "6379"
      REDIS_AUTH: ${REDIS_AUTH}

      LANGFUSE_S3_EVENT_UPLOAD_BUCKET: langfuse
      LANGFUSE_S3_EVENT_UPLOAD_REGION: auto
      LANGFUSE_S3_EVENT_UPLOAD_ACCESS_KEY_ID: minio
      LANGFUSE_S3_EVENT_UPLOAD_SECRET_ACCESS_KEY: ${MINIO_ROOT_PASSWORD}
      LANGFUSE_S3_EVENT_UPLOAD_ENDPOINT: http://minio:9000
      LANGFUSE_S3_EVENT_UPLOAD_FORCE_PATH_STYLE: "true"
      LANGFUSE_S3_EVENT_UPLOAD_PREFIX: events/

      LANGFUSE_S3_MEDIA_UPLOAD_BUCKET: langfuse
      LANGFUSE_S3_MEDIA_UPLOAD_REGION: auto
      LANGFUSE_S3_MEDIA_UPLOAD_ACCESS_KEY_ID: minio
      LANGFUSE_S3_MEDIA_UPLOAD_SECRET_ACCESS_KEY: ${MINIO_ROOT_PASSWORD}
      LANGFUSE_S3_MEDIA_UPLOAD_ENDPOINT: http://minio:9000
      LANGFUSE_S3_MEDIA_UPLOAD_FORCE_PATH_STYLE: "true"
      LANGFUSE_S3_MEDIA_UPLOAD_PREFIX: media/
    restart: unless-stopped

  langfuse-web:
    image: langfuse/langfuse:3
    depends_on:
      langfuse-worker:
        condition: service_started
    ports:
      - "0.0.0.0:3000:3000"
    environment:
      <<: *langfuse-common
      NEXTAUTH_URL: http://localhost:3000
      NEXTAUTH_SECRET: ${LANGFUSE_SECRET}

      LANGFUSE_INIT_ORG_ID: ${LANGFUSE_INIT_ORG_ID}
      LANGFUSE_INIT_ORG_NAME: ${LANGFUSE_INIT_ORG_NAME}
      LANGFUSE_INIT_PROJECT_ID: ${LANGFUSE_INIT_PROJECT_ID}
      LANGFUSE_INIT_PROJECT_NAME: ${LANGFUSE_INIT_PROJECT_NAME}
      LANGFUSE_INIT_PROJECT_PUBLIC_KEY: ${LANGFUSE_INIT_PROJECT_PUBLIC_KEY}
      LANGFUSE_INIT_PROJECT_SECRET_KEY: ${LANGFUSE_INIT_PROJECT_SECRET_KEY}
      LANGFUSE_INIT_USER_EMAIL: ${LANGFUSE_INIT_USER_EMAIL}
      LANGFUSE_INIT_USER_NAME: ${LANGFUSE_INIT_USER_NAME}
      LANGFUSE_INIT_USER_PASSWORD: ${LANGFUSE_INIT_USER_PASSWORD}
    restart: unless-stopped

  thinktank:
    build: ./app
    environment:
      ANTHROPIC_API_KEY: ${ANTHROPIC_API_KEY}
      TAVILY_API_KEY: ${TAVILY_API_KEY}
      DATABASE_URL: postgresql://thinktank:${POSTGRES_PASSWORD}@postgres:5432/thinktank
      LANGFUSE_HOST: http://langfuse-web:3000
      LANGFUSE_PUBLIC_KEY: ${LANGFUSE_INIT_PROJECT_PUBLIC_KEY}
      LANGFUSE_SECRET_KEY: ${LANGFUSE_INIT_PROJECT_SECRET_KEY}
    ports:
      - "0.0.0.0:8000:8000"
    depends_on:
      postgres:
        condition: service_healthy
      langfuse-web:
        condition: service_started
    restart: unless-stopped
```

### `init.sql`

```sql
CREATE DATABASE langfuse;

\c thinktank

CREATE TABLE IF NOT EXISTS runs (
  id UUID PRIMARY KEY,
  idea TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'queued',
  result TEXT,
  error TEXT,
  token_cost_usd NUMERIC(10, 4),
  created_at TIMESTAMPTZ DEFAULT NOW(),
  completed_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS runs_status_idx ON runs(status);
CREATE INDEX IF NOT EXISTS runs_created_idx ON runs(created_at DESC);
```

### `app/Dockerfile`

```dockerfile
FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
```

### `app/requirements.txt`

Pin these versions. They work together as of April 2026 — resist the urge to unpin:

```
crewai[anthropic]>=1.14,<2
crewai-tools>=1.14
tavily-python>=0.5
openlit==1.35.0
langfuse>=3.0,<5
fastapi>=0.115
uvicorn[standard]>=0.32
psycopg[binary]>=3.2
pydantic>=2.9
```

**About the choices:** `crewai[anthropic]` installs the native Anthropic provider (faster than LiteLLM, better tool handling). OpenLit 1.35 is the version where the `async_agno.py` syntax bug was fixed. Langfuse v3 SDK does OTel natively.

### `app/thinktank.py`

Critical: `openlit.init()` must run **before** any framework import:

```python
"""
CrewAI-based virtual startup think tank.
Six agents in sequence turn an idea into an executive brief.

IMPORTANT: OpenLit must be initialized before importing CrewAI, Langfuse,
or anything else that sets up OpenTelemetry. CrewAI registers its own
telemetry collector at import time; OpenLit needs to patch the anthropic
SDK first so LLM calls get instrumented.
"""
import os
import base64

# --- OpenLit (must be first) ---------------------------------------
import openlit

_auth = f"{os.environ['LANGFUSE_PUBLIC_KEY']}:{os.environ['LANGFUSE_SECRET_KEY']}"
_b64 = base64.b64encode(_auth.encode()).decode()

openlit.init(
    otlp_endpoint=f"{os.environ['LANGFUSE_HOST']}/api/public/otel",
    otlp_headers={"Authorization": f"Basic {_b64}"},
    application_name="thinktank",
)

# --- Everything else ----------------------------------------------
from langfuse import observe
from crewai import Agent, Task, Crew, Process, LLM
from crewai_tools import TavilySearchTool

# --- Model tiers --------------------------------------------------
# NOTE on model strings: CrewAI's LLM factory picks native vs. LiteLLM
# based purely on the string prefix. Plain "anthropic/..." routes to
# the native Anthropic provider. Don't add `use_native=False` (ignored)
# or `litellm/anthropic/...` (LiteLLM chokes on the double provider).
# NOTE on temperature: Opus 4.7 rejects `temperature` as deprecated.
# Only set it for Sonnet and Haiku.
opus = LLM(model="anthropic/claude-opus-4-7", max_tokens=4096)
sonnet = LLM(model="anthropic/claude-sonnet-4-6", temperature=0.4, max_tokens=4096)
haiku = LLM(model="anthropic/claude-haiku-4-5-20251001", temperature=0.5, max_tokens=2048)

# --- Tools --------------------------------------------------------
search = TavilySearchTool(search_depth="advanced", max_results=7)

# --- Agents -------------------------------------------------------
ceo = Agent(
    role="CEO and Orchestrator",
    goal=("Turn a raw idea into a cohesive, prioritized launch plan by synthesizing "
          "input from the team and cutting what doesn't matter."),
    backstory=("You've founded three companies, killed two of them early, and know "
               "that a one-page plan beats a fifty-page plan. You push back on fluff "
               "and demand clear GO/NO-GO/PIVOT calls."),
    llm=opus,
    allow_delegation=False,
    verbose=True,
)

product = Agent(
    role="Product Strategist",
    goal=("Define the smallest version of the idea that proves the core hypothesis, "
          "and a phased roadmap beyond it."),
    backstory=("Ex-PM at an infrastructure company. You think in ICP, jobs-to-be-done, "
               "and 'what single metric tells us this is working?'"),
    llm=sonnet,
    verbose=True,
)

cto = Agent(
    role="CTO and Architect",
    goal=("Recommend a pragmatic tech stack, deployment model, and identify the top 3 "
          "technical risks. Prefer boring, proven technology."),
    backstory=("You run infrastructure for a living. You've seen Kubernetes eat "
               "weekends and you know when a single VPS is the right answer."),
    llm=sonnet,
    verbose=True,
)

growth = Agent(
    role="Head of Growth",
    goal=("Define positioning, target channels, and a launch plan with a clear "
          "first-100-users strategy."),
    backstory=("You've launched B2B SaaS and dev tools. You know HN, Reddit, and cold "
               "outbound each need different messaging. You're skeptical of 'viral' "
               "as a strategy."),
    llm=sonnet,
    verbose=True,
)

analyst = Agent(
    role="Competitive Intelligence Analyst",
    goal=("Map the competitive landscape, identify direct and indirect competitors, "
          "and find the 3 most honest differentiation angles."),
    backstory=("You do desk research fast and you're suspicious of claims. You cite "
               "sources. You flag when a 'gap in the market' is actually a graveyard "
               "of dead startups."),
    llm=haiku,
    tools=[search],
    verbose=True,
)

devil = Agent(
    role="Devil's Advocate",
    goal=("Find the reasons this idea will fail. Attack assumptions, challenge the "
          "TAM, identify the quiet killer risks no one wants to name."),
    backstory=("You've watched confident founders burn through savings on ideas that "
               "were obviously flawed in retrospect. You're not mean, you're honest. "
               "Your job is to save this founder from themselves."),
    llm=opus,
    verbose=True,
)


# --- Tasks (sequential, each agent sees previous outputs) ---------
def build_tasks(idea: str):
    return [
        Task(
            description=(
                f"Research the market for this idea:\n\n{idea}\n\n"
                "Identify:\n"
                "  1. Direct competitors (with pricing where public)\n"
                "  2. Indirect substitutes and adjacent players\n"
                "  3. Real market-size signal (not TAM fantasy)\n"
                "  4. Three differentiation angles, ranked by defensibility\n\n"
                "Cite sources. Flag dead-competitor graveyards."
            ),
            expected_output="Structured market brief with cited sources.",
            agent=analyst,
        ),
        Task(
            description=(
                f"Define MVP scope for: {idea}\n\n"
                "Using the market brief above, propose:\n"
                "  1. The single hypothesis the MVP must validate\n"
                "  2. In-scope and out-of-scope features\n"
                "  3. A 3-phase roadmap (MVP / v1 / v2)\n"
                "Max two pages equivalent. No feature lists without rationale."
            ),
            expected_output="PRD-style document, concise.",
            agent=product,
        ),
        Task(
            description=(
                f"Design the technical approach for: {idea}\n\n"
                "Given the MVP scope, propose a stack, hosting model, and data "
                "architecture. Justify each choice in one sentence. Flag the top 3 "
                "technical risks and how to de-risk each in the MVP.\n"
                "Prefer boring tech. Call out when something is speculative."
            ),
            expected_output="Architecture memo with a simple text component diagram.",
            agent=cto,
        ),
        Task(
            description=(
                f"Design the go-to-market for: {idea}\n\n"
                "Output:\n"
                "  1. ICP definition (one paragraph, concrete)\n"
                "  2. Positioning statement\n"
                "  3. Top 3 channels ranked by ICP fit\n"
                "  4. First-100-users tactical plan (week 1 actions)\n"
                "  5. Pricing hypothesis with reasoning"
            ),
            expected_output="GTM plan with concrete first-week actions.",
            agent=growth,
        ),
        Task(
            description=(
                f"Red-team the entire plan for: {idea}\n\n"
                "Review all outputs above. Produce a risk register:\n"
                "  1. Top 5 reasons this fails\n"
                "  2. The assumption each reason attacks\n"
                "  3. The cheapest test to falsify each assumption before investing\n\n"
                "Be brutal. Surface the risks no one else named."
            ),
            expected_output="Risk register, brutally honest.",
            agent=devil,
        ),
        Task(
            description=(
                f"Synthesize everything into a final executive brief for: {idea}\n\n"
                "Include:\n"
                "  1. One-paragraph thesis\n"
                "  2. MVP scope (3 bullets max)\n"
                "  3. Stack choice (one line)\n"
                "  4. GTM summary (3 bullets max)\n"
                "  5. Top 3 risks\n"
                "  6. A clear GO / NO-GO / PIVOT call with reasoning\n\n"
                "Max one page equivalent. Be decisive."
            ),
            expected_output="Executive brief, max one page, decisive.",
            agent=ceo,
        ),
    ]


@observe(name="thinktank_run")
def run_thinktank(idea: str) -> str:
    crew = Crew(
        agents=[analyst, product, cto, growth, devil, ceo],
        tasks=build_tasks(idea),
        process=Process.sequential,
        verbose=True,
    )
    return str(crew.kickoff())
```

### `app/main.py`

```python
"""
FastAPI wrapper. Submit idea → runs crew in background → persists to Postgres.
"""
import os
import uuid
from datetime import datetime, timezone

import psycopg
from fastapi import FastAPI, BackgroundTasks, HTTPException
from pydantic import BaseModel

# Import AFTER any OTel setup — thinktank.py handles openlit init
from thinktank import run_thinktank

app = FastAPI(title="Think Tank", version="0.2")
DB = os.environ["DATABASE_URL"]


class IdeaIn(BaseModel):
    idea: str


class RunOut(BaseModel):
    run_id: str
    status: str
    idea: str | None = None
    result: str | None = None
    error: str | None = None
    created_at: datetime | None = None
    completed_at: datetime | None = None


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/run", response_model=RunOut)
def submit(body: IdeaIn, bg: BackgroundTasks):
    if not body.idea.strip():
        raise HTTPException(400, "idea is empty")
    run_id = str(uuid.uuid4())
    with psycopg.connect(DB) as c, c.cursor() as cur:
        cur.execute(
            "INSERT INTO runs (id, idea, status) VALUES (%s, %s, 'running')",
            (run_id, body.idea),
        )
        c.commit()
    bg.add_task(_execute, run_id, body.idea)
    return RunOut(run_id=run_id, status="running", idea=body.idea)


@app.get("/run/{run_id}", response_model=RunOut)
def status(run_id: str):
    with psycopg.connect(DB) as c, c.cursor() as cur:
        cur.execute(
            "SELECT id, idea, status, result, error, created_at, completed_at "
            "FROM runs WHERE id = %s",
            (run_id,),
        )
        row = cur.fetchone()
    if not row:
        raise HTTPException(404, "not found")
    return RunOut(
        run_id=str(row[0]), idea=row[1], status=row[2],
        result=row[3], error=row[4],
        created_at=row[5], completed_at=row[6],
    )


@app.get("/runs")
def list_runs(limit: int = 20):
    with psycopg.connect(DB) as c, c.cursor() as cur:
        cur.execute(
            "SELECT id, idea, status, created_at FROM runs "
            "ORDER BY created_at DESC LIMIT %s",
            (limit,),
        )
        rows = cur.fetchall()
    return [
        {"run_id": str(r[0]), "idea": r[1][:120], "status": r[2], "created_at": r[3]}
        for r in rows
    ]


def _execute(run_id: str, idea: str):
    try:
        result = run_thinktank(idea)
        status_val, error = "done", None
    except Exception as e:  # noqa: BLE001
        result, status_val, error = None, "failed", str(e)
    with psycopg.connect(DB) as c, c.cursor() as cur:
        cur.execute(
            "UPDATE runs SET status=%s, result=%s, error=%s, completed_at=%s "
            "WHERE id=%s",
            (status_val, result, error, datetime.now(timezone.utc), run_id),
        )
        c.commit()
```

---

## Part 6 — Launch

```bash
cd ~/thinktank
docker compose up -d
```

**First boot takes 3–5 minutes** — ClickHouse runs migrations, MinIO bucket gets created, Langfuse schema initializes, pip builds the Python image.

Watch progress:

```bash
docker compose logs -f langfuse-web
```

Wait for `Ready in ...ms`.

Verify everything is up:

```bash
docker compose ps
```

You should see eight services running: postgres, clickhouse, redis, minio, langfuse-worker, langfuse-web, thinktank, plus minio-init which will show "Exited (0)" (it ran, created the bucket, and exited — that's correct).

Health checks:

```bash
curl http://localhost:8000/health              # {"status":"ok"}
curl -I http://localhost:3000                  # HTTP/1.1 200 OK
```

---

## Part 7 — Configure Langfuse (one-time)

### Log in

Open `http://thinktank.local:3000` in your Mac browser. Sign in with the email/password from `.env`. Your org, project, and API keys are already set up via the `LANGFUSE_INIT_*` vars.

### Add model pricing

Langfuse doesn't know Claude prices by default. In the UI: **Settings → Models → + Add model definition**. Add these three (check `https://www.anthropic.com/pricing` for current numbers):

| Model name | Match pattern | Input / token | Output / token | Tokenizer |
|---|---|---|---|---|
| Claude Opus 4.7 | `(?i)claude-opus-4-7` | 0.000015 | 0.000075 | Anthropic |
| Claude Sonnet 4.6 | `(?i)claude-sonnet-4-6` | 0.000003 | 0.000015 | Anthropic |
| Claude Haiku 4.5 | `(?i)claude-haiku-4-5` | 0.000001 | 0.000005 | Anthropic |

Unit: TOKENS. `(?i)` makes the regex case-insensitive.

---

## Part 8 — First real run

From macOS:

```bash
curl -X POST http://thinktank.local:8000/run \
  -H "Content-Type: application/json" \
  -d '{"idea": "A CLI tool that audits Proxmox clusters for security posture and emits a CIS-style report."}'
```

Response gives you a `run_id`. Poll for completion:

```bash
curl http://thinktank.local:8000/run/<run_id>
```

Runs take 4–7 minutes. When `status` is `done`, the `result` field contains the CEO's final brief.

List recent runs:

```bash
curl http://thinktank.local:8000/runs | jq
```

---

## Part 9 — What you should see in Langfuse

Open the trace in Langfuse UI. The structure:

```
thinktank_run  (outer @observe span)
└── invoke_agent crew
    ├── invoke_agent task (Analyst)
    │   └── invoke_agent Competitive Intelligence Analyst
    │       ├── chat claude-haiku-4-5 ...  ← tokens + cost
    │       ├── tavily_search  ← tokens + cost
    │       └── tavily_search  ← tokens + cost
    ├── invoke_agent task (Product)
    │   └── invoke_agent Product Strategist
    │       └── chat claude-sonnet-4-6 ...  ← tokens + cost
    ├── invoke_agent task (CTO)
    │   └── ... etc
    ├── invoke_agent task (Growth)
    ├── invoke_agent task (Devil)
    └── invoke_agent task (CEO)
```

Each generation span will show: model, input tokens, output tokens, latency, and cost computed from your pricing table. Total cost rolls up at the trace level — click into the trace header to see `$1.10` or whatever the total came to.

A typical run breakdown:
- Analyst (Haiku + 2 Tavily searches): ~30k tokens, ~$0.04
- Product / CTO / Growth (Sonnet, each): ~5–10k tokens, ~$0.03–0.08
- Devil's Advocate (Opus): ~15–20k tokens, ~$0.40–0.50
- CEO synthesis (Opus): ~15–20k tokens, ~$0.30–0.40

**Take Parallels snapshot now: `observability-working`.** Everything is tested and working — this is your rollback anchor.

---

## Part 10 — Day-two operations

### Commands

```bash
docker compose logs -f thinktank                  # tail app
docker compose up -d --build thinktank            # rebuild after code changes
docker compose stop                               # stop everything (safe for VM sleep)
docker compose start                              # bring back
docker compose exec -T thinktank python -c "..."  # run python in the app container
                                                  # -T flag is required for heredoc input
```

### Tuning agents

The quality of outputs lives in the agent **backstories** and task descriptions, not the framework. To tune:

1. Edit `app/thinktank.py`
2. `docker compose up -d --build thinktank`
3. Submit the same idea twice (before and after), compare in Langfuse

Focus on the Devil's Advocate first — it's the highest-leverage agent. Make its backstory meaner and more specific. The difference in critique quality is striking.

### Sleep/wake

Close MacBook lid → Parallels suspends VM → any in-progress run pauses → resumes on wake. That's fine for async workflows. If a background run dies during suspension:

```bash
docker compose restart thinktank
```

Then resubmit.

### Memory pressure

If macOS Memory goes yellow with the VM at 8GB, drop the VM to 6GB. The workload doesn't need 8.

### Backups

Parallels snapshots give you instant rollback. For longer-term protection:

```bash
# Weekly Postgres dump, run via cron inside VM
docker compose exec -T postgres pg_dump -U thinktank thinktank > \
  ~/backups/runs-$(date +%F).sql
```

Project files are lightweight, rsync them to your Mac periodically:

```bash
# From macOS
rsync -az --exclude 'data/' drmilosz@thinktank.local:~/thinktank/ ~/backups/thinktank/
```

### Cleaning up ClickHouse data

Langfuse has a built-in data retention feature to auto-delete old traces. Set it in the UI: **Settings → Data Retention**. For personal use, 90 days is plenty and keeps ClickHouse small.

---

## Part 11 — Troubleshooting (lessons from the session)

### `docker compose exec` fails with "cannot attach stdin to a TTY"

When piping a heredoc or file into `exec`, add the `-T` flag to disable pseudo-TTY:

```bash
docker compose exec -T thinktank python << 'EOF'
...
EOF
```

### Tokens not appearing in Langfuse

Verify OpenLit is loaded and the provider is being instrumented:

```bash
docker compose exec -T thinktank python << 'EOF'
import os, base64, openlit
auth = f"{os.environ['LANGFUSE_PUBLIC_KEY']}:{os.environ['LANGFUSE_SECRET_KEY']}"
openlit.init(
    otlp_endpoint=f"{os.environ['LANGFUSE_HOST']}/api/public/otel",
    otlp_headers={"Authorization": f"Basic {base64.b64encode(auth.encode()).decode()}"},
    application_name="diag-test",
)
from anthropic import Anthropic
r = Anthropic().messages.create(
    model="claude-haiku-4-5-20251001",
    max_tokens=20,
    messages=[{"role": "user", "content": "say pong"}]
)
print("tokens in:", r.usage.input_tokens, "out:", r.usage.output_tokens)
import time; time.sleep(5)
EOF
```

Then check Langfuse for a trace with `application_name: diag-test`. If tokens show up there but not in CrewAI runs, OpenLit isn't running before CrewAI imports — double-check the order in `thinktank.py`.

### MinIO "NoSuchBucket" errors from Langfuse

The `minio-init` init container should've created the bucket. If you see `NoSuchBucket` errors, run it manually:

```bash
source ~/thinktank/.env
docker compose exec -T minio \
  mc alias set local http://localhost:9000 minio "$MINIO_ROOT_PASSWORD"
docker compose exec -T minio mc mb local/langfuse
```

### Langfuse "Failed to fetch latest releases" noise

Harmless. Langfuse checks GitHub for updates every ~7s and fails if the VM has restricted internet. Ignore the logs.

### `temperature` deprecated error

Opus 4.7 doesn't accept `temperature`. Only set it on Sonnet and Haiku. (Already correct in the thinktank.py above, but worth knowing when newer frontier models come out.)

### CrewAI `ImportError: Anthropic native provider not available`

`crewai[anthropic]` extra isn't installed. Check `requirements.txt` — should be `crewai[anthropic]>=1.14,<2` not plain `crewai`.

### CrewAI uses native but I want LiteLLM

The `litellm/anthropic/...` prefix is a CrewAI factory flag, but LiteLLM itself rejects the doubled provider string. Can't cleanly swap to LiteLLM in CrewAI 1.14+. The native provider path works fine with OpenLit for observability — that's the recommended setup.

---

## Part 12 — What to add when you feel the need

Priority order:

1. **Adjust pricing entries** as Anthropic's prices change or new models ship. Takes 2 minutes.
2. **Streamlit web UI** for submitting ideas from your phone. 50 lines of code.
3. **RAG over your notes** — if you find yourself wishing the CTO agent knew about your Proxmox history or past project decisions. Adds Qdrant back (which we removed in this version as unused).
4. **Telegram front-end** — the "message your think tank from anywhere" workflow. Python bot polling `/run`.
5. **Hierarchical process** — swap `Process.sequential` to `Process.hierarchical`. Faster, more expensive, parallel agents. Only after you've run 20+ ideas and understand the sequential output deeply.

---

## Quick reference

| Thing | Where |
|---|---|
| Submit idea | `POST http://thinktank.local:8000/run` |
| Check run | `GET http://thinktank.local:8000/run/{id}` |
| List runs | `GET http://thinktank.local:8000/runs` |
| Langfuse UI | `http://thinktank.local:3000` |
| MinIO console | `http://thinktank.local:9001` (user: `minio`) |
| App logs | `docker compose logs -f thinktank` |
| Project files | `~/thinktank/` (inside VM) |
| VM bundle | `~/Parallels/thinktank.pvm` (on macOS) |
| Snapshots | Parallels → thinktank → Actions → Snapshots |

---

## About this version

Previous version of this doc assumed Langfuse v2 with Postgres-only, and claimed CrewAI's OTel or LiteLLM fallback would just work with a few env vars. Both were wrong in ways that cost a full debugging session. Corrections in this version:

- **Langfuse v2 → v3**: v3 is required for OTLP ingestion, which is what LiteLLM and OpenLit emit.
- **LiteLLM callbacks → OpenLit**: CrewAI 1.14+ routes to the native Anthropic SDK by string prefix; LiteLLM callbacks never fire. OpenLit patches the anthropic SDK directly.
- **OpenLit init ordering**: must run before CrewAI import or CrewAI's own telemetry wins the tracer race.
- **OpenLit version pin (`==1.35.0`)**: version 1.33 had a syntax bug in `async_agno.py` that killed init silently.
- **MinIO bucket init container**: MinIO doesn't auto-create buckets, Langfuse v3 expects one.
- **Model string format**: plain `anthropic/claude-...`, no `use_native=False`, no `litellm/` prefix.
- **Temperature on Opus 4.7**: removed, deprecated.
- **Qdrant removed**: was scaffolding for an unused RAG feature.
- **`docker compose exec -T`**: needed for piping input into the container.

Current and working as of April 18, 2026.
