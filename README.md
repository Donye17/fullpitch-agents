# Fullpitch ADK Agents

Google ADK Python agents for the Fullpitch US rugby data platform. Deployed on Railway.

## Setup

```bash
python -m venv .venv
source .venv/bin/activate  # or .venv\Scripts\activate on Windows
pip install -r requirements.txt
cp .env.example .env
# Fill in your API keys in .env
```

## Usage

```bash
# Run the boss agent (orchestrates all sub-agents)
python main.py

# Run a specific agent
python main.py --agent mlr
python main.py --agent news
python main.py --agent video
python main.py --agent eagles
python main.py --agent college
python main.py --agent content
python main.py --agent wer
```

## Agents

| Agent | Job | Frequency |
|-------|-----|-----------|
| `boss` | Orchestrates all sub-agents | Hourly |
| `mlr` | MLR scores, standings, player stats | Hourly Feb-Oct |
| `news` | Article ingest + US rugby relevance filter | Hourly |
| `video` | YouTube content discovery | Daily 6am UTC |
| `eagles` | USA Eagles results | Daily 7am UTC |
| `college` | College scores + standings | Every 2 hours |
| `content` | AI content writer (reports, recaps) | Triggered |
| `wer` | World Rugby Rankings | Daily 8am UTC |

## Gemini Models

| Constant | Model | Use |
|----------|-------|-----|
| `GEMINI_REASONING` | `gemini-2.5-flash` | Agent logic, classification, dedup |
| `GEMINI_WRITING_MID` | `gemini-2.5-flash` | Summaries, recaps |
| `GEMINI_WRITING_PRO` | `gemini-2.5-flash` | Match reports, spotlights |

## Architecture

```
Railway Cron → Boss Agent → Sub-agents → Fullpitch REST API → Neon DB
```

All agents write through the Fullpitch app's `/api/v1/ingest/` endpoints, authenticated with `FULLPITCH_API_KEY`.

## Deployment

Deployed on Railway ($5/month). Configuration in `railway.json`.
