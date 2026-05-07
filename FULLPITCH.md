# FULLPITCH — Project Memory & Plan

> Drop this file in the root of every repo: `rugby-platform/` and `fullpitch-agents/`
> Cursor / Claude Code must read this at the start of every session — no exceptions.

---

## Instructions for AI Assistants

- **Read this file first** before every session
- **Update "Current State"** after completing any major step
- **Update "Decisions Log"** at the end of every session with date + summary
- **Never run `prisma migrate reset`** without confirming `exports/videos-backup.json` exists
- **Never run `prisma db push`** without checking Current State first
- **Never suggest TypeScript agents or Cloudflare Workers** for the agent layer — ADK on Railway is locked
- **Always use explicit `@relation` names** in Prisma — ambiguous relations fail validation
- **Commit before every DB operation**: `git add . && git commit -m "..."` first
- **Never add Prisma models** without checking for relation ambiguity
- **API routes must be versioned** `/api/v1/` — mobile app is planned

---

## What Is Fullpitch?

Fullpitch (`fullpitch.app`) is the only comprehensive US rugby data platform — covering every level: MLR (Major League Rugby), College (CRAA + NCR), Club, High School, and USA Eagles. It tracks scores, standings, players, stats, articles, and video.

**The gap:** MLR has a basic app. USA Rugby has almost nothing. No one covers college, club, or high school at scale. Fullpitch is building what nobody else is.

**Revenue paths:** Ads/sponsorships, API access for broadcasters/fantasy/journalists, premium subscriptions, eventual USA Rugby data partnership.

**Future:** React Native mobile app. REST API must be built mobile-ready from day one — versioned, consistent response shapes, proper pagination, Clerk JWT auth.

---

## Tech Stack

| Layer | Tool | Notes |
|-------|------|-------|
| Framework | Next.js 15 (App Router) | Fresh install, TypeScript |
| Database | PostgreSQL via Neon | Already provisioned |
| ORM | Prisma | Schema already validated — 31 models |
| Auth | Clerk | Roles: admin, program_rep, user |
| UI | Tailwind CSS + shadcn/ui | Mobile-first, always |
| Error tracking | Sentry | Free tier only |
| Analytics | Vercel Analytics | Built into Pro plan — enable it |
| Hosting | Vercel Pro | Already paying |
| Staging | Vercel Preview + Neon DB branch | Free — separate from production |
| Agent hosting | Railway | $5/month — Python, always-on |
| Agent framework | Google ADK (Python) | Multi-agent orchestration |
| Agent model — reasoning | `gemini-2.5-flash-lite` | High volume, cheap, free tier |
| Agent model — writing mid | `gemini-2.5-flash` | Summaries, recaps |
| Agent model — writing pro | `gemini-2.5-pro` | Match reports, spotlights |

**Repo locations (local):**
- App: `C:\Users\josh\Desktop\rugby-platform` (new, clean install)
- Agents: `C:\Users\josh\Desktop\fullpitch-agents` (separate repo)

> [!important] Locked Decisions — Never Change
> - Agents = Google ADK Python on Railway. Never TypeScript. Never Cloudflare Workers.
> - `gemini-2.0-flash` is deprecated — shuts down June 1 2026. Never use it.
> - Use only: `gemini-2.5-flash-lite`, `gemini-2.5-flash`, `gemini-2.5-pro`
> - UI = Tailwind + shadcn/ui. Mobile-first always.
> - Auth = Clerk. Three roles: `admin`, `program_rep`, `user`
> - All REST API routes versioned: `/api/v1/`

---

## Environment Variables

### App — Vercel (production + staging)

```env
# Database
DATABASE_URL=                         # Neon production connection string
STAGING_DATABASE_URL=                 # Neon staging branch connection string

# Auth — Clerk
NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY=
CLERK_SECRET_KEY=

# Internal API security
FULLPITCH_API_KEY=                    # Random string — protects ingest endpoints from agents

# Monitoring
NEXT_PUBLIC_SENTRY_DSN=
SENTRY_AUTH_TOKEN=

# Environment
NEXT_PUBLIC_SITE_ENV=production       # or "staging"
```

### Agents — Railway

```env
GOOGLE_API_KEY=                       # Google AI Studio — single key, multiple models
YOUTUBE_DATA_API_KEY=                 # Google Cloud Console — YouTube Data API v3, free
FULLPITCH_API_URL=https://fullpitch.app
FULLPITCH_API_KEY=                    # Same key as app above
SENTRY_DSN=                           # Agent error tracking
```

### Where to Get Keys

| Key | Source | Cost |
|-----|--------|------|
| `GOOGLE_API_KEY` | aistudio.google.com → Get API key | Free |
| `YOUTUBE_DATA_API_KEY` | Google Cloud Console → YouTube Data API v3 | Free tier |
| `CLERK_*` | clerk.com → Create application | Free up to 10k users |
| `FULLPITCH_API_KEY` | Generate any long random string | Free |
| `SENTRY_DSN` | sentry.io → Create project → free tier | Free |

---

## Database Schema — 31 Models, 16 Enums

Schema is finalized and validated. It is the canonical source of truth. Saved from old project.

### Relation Rules — Never Break

- All ambiguous relations have explicit `@relation` names
- `Match → Team`: `HomeTeam` and `AwayTeam` (named)
- `Player → Team`: `currentTeam`, `fromTeam`, `toTeam` (named)
- `Team → League`: named relation
- Every FK has a corresponding index
- All models with `updatedAt` use `@updatedAt`

### Source + Verification Fields

Every model that accepts submissions needs these fields:

```prisma
submittedBy    String?    // userId or "agent:mlr-agent"
verifiedBy     String?    // admin userId who approved
source         String?    // "agent" | "program_rep" | "admin" | "community"
verifiedAt     DateTime?
```

### Key Models

| Model | Purpose | Data Source |
|-------|---------|------------|
| `Article` | News content | news-agent |
| `Team` | All teams | mlr-agent, program_rep, scripts |
| `Player` | Player profiles | mlr-agent, program_rep |
| `PlayerSeason` | Per-season stats | mlr-agent |
| `Match` | All games/results | mlr-agent, program_rep |
| `MatchEvent` | In-game events | Phase 2 |
| `Standing` | League tables | mlr-agent |
| `Video` | YouTube content | video-agent |
| `DataConflict` | Flags for review | All agents |
| `NationalTeam` | USA Eagles | eagles-agent |
| `NationalTeamResult` | Eagles results | eagles-agent |
| `WorldRankingEntry` | World rankings | wer-agent |
| `TerritorialUnion` | Regional bodies | scraper |
| `ClubTeam` | Club teams | scraper |
| `ClubStanding` | Club standings | scraper needed |
| `StateAssociation` | State bodies | scraper needed |
| `HighSchoolProgram` | HS programs | Phase 2 |
| `CollegeProgram` | College programs | seed script |
| `AgentMemory` | Agent state | agent layer |
| `AgentTask` | Agent task queue | agent layer |
| `AgentMessage` | Agent message log | agent layer |
| `AgentLog` | Run history | agent layer |

---

## Tagging System

Every content record tagged at ingest. Use only these exact strings:

| Field | Valid Values |
|-------|-------------|
| `league` | `mlr` `craa-d1a` `craa-d1aa` `craa-women` `ncr-d1` `ncr-d2` `ncr-d3` `ncr-women` `club` `high-school` `eagles` `world` |
| `level` | `professional` `college` `club` `high-school` `international` |
| `region` | `northeast` `southeast` `midwest` `southwest` `west` `national` |
| `status` | `live` `final` `scheduled` `postponed` `cancelled` |
| `season` | `"2024"` `"2025"` `"2026"` |
| `source` | `agent` `program_rep` `admin` `community` |

---

## User Roles & Permissions

| Role | Can Do |
|------|--------|
| `admin` | Everything — full CMS, approve submissions, manage users, view agent logs |
| `program_rep` | Manage own team page — roster, scores, stats, schedule |
| `user` | Follow teams, submit corrections (goes to moderation queue) |

### Program Rep System — Option C Hybrid

- **Anyone** can submit corrections or new data → goes to admin moderation queue
- **Coaches/ADs** can claim their program → verified via .edu or official org email
- **Verified `program_rep`** gets edit access to their own team page without approval needed
- **Moderation queue** at `/admin/moderation` for unverified submissions

### Program Claim Flow

1. User clicks "Claim this program" on any team page
2. Submits: name, role, email address
3. System sends verification email
4. Admin reviews and approves → user gets `program_rep` role + linked `programId`
5. Rep can now manage: roster, match results, stats, upcoming schedule, team info

### What Program Reps Can Submit

- Player roster + profiles
- Match results + scores
- Season stats
- Upcoming schedule
- Team info (colors, home field, head coach, website)

---

## Content Agent — AI-Written Content

Agents ingest data AND write content from it. All prompts live in versioned files — never hardcoded.

| Content Type | Model | Trigger | Publish Mode |
|-------------|-------|---------|-------------|
| Match report (MLR) | `gemini-2.5-pro` | After final score written | Draft → admin approves |
| Match report (college) | `gemini-2.5-flash` | After score written | Draft → admin approves |
| Article summary | `gemini-2.5-flash` | On article ingest | Auto-publish |
| Standings recap | `gemini-2.5-flash` | Weekly | Draft → admin approves |
| Player spotlight | `gemini-2.5-pro` | Manual trigger | Draft → admin approves |

Prompts folder: `fullpitch-agents/prompts/`
- `match_report_mlr.txt`
- `match_report_college.txt`
- `article_summary.txt`
- `standings_recap.txt`
- `player_spotlight.txt`

---

## Agents Architecture

> [!important] Locked In Forever
> Framework: Google ADK Python | Host: Railway ($5/month)
> Never rewrite in TypeScript. Never move to Cloudflare Workers.

### Flow

```
Railway Cron → Boss Agent
                    ↓
       Delegates to sub-agents
                    ↓
    Each agent reasons before writing:
    "Is this US rugby content?"
    "Does this conflict with existing data?"
    "Is this player already in the DB?"
    "Can I link this article to a match?"
                    ↓
    Calls Fullpitch REST API (/api/v1/)
                    ↓
              Neon DB (production only)
```

### Structure

```
fullpitch-agents/
├── agents/
│   ├── boss_agent.py        ← orchestrator, runs all sub-agents
│   ├── mlr_agent.py         ← MLR scores, standings, player stats
│   ├── news_agent.py        ← article ingest + US rugby relevance filter
│   ├── video_agent.py       ← YouTube content discovery
│   ├── eagles_agent.py      ← USA Eagles results
│   ├── college_agent.py     ← college scores + standings
│   ├── content_agent.py     ← AI content writer (reports, recaps)
│   ├── wer_agent.py         ← Women's Elite Rugby scores + standings
│   └── world_rankings_agent.py ← World Rugby Rankings (USA position)
├── tools/
│   ├── fullpitch_api.py     ← REST API wrapper (read + write)
│   ├── search.py            ← web search utility
│   └── scraper.py           ← HTML fetch + parse, rate limiting
├── prompts/
│   ├── match_report_mlr.txt
│   ├── match_report_college.txt
│   ├── article_summary.txt
│   ├── standings_recap.txt
│   └── player_spotlight.txt
├── main.py                  ← entry: python main.py --agent [name]
├── requirements.txt         ← google-adk, httpx, beautifulsoup4, python-dotenv, sentry-sdk
├── railway.json             ← start: python main.py, cron: "0 * * * *"
├── .env.example
└── README.md
```

### Agent Schedule

| Agent | Frequency | Job |
|-------|-----------|-----|
| `mlr-agent` | Hourly Feb–Oct | Scores, standings, player stats |
| `news-agent` | Hourly | Article ingest + filter |
| `video-agent` | Daily 6am UTC | YouTube discovery |
| `eagles-agent` | Daily 7am UTC | USA Eagles results |
| `college-agent` | Every 2 hours | College scores + standings |
| `wer-agent` | Hourly during WER season | Women's Elite Rugby scores + standings |
| `world-rankings-agent` | Daily 8am UTC | World rankings |
| `content-agent` | Triggered by other agents | Match reports, recaps |

### Gemini Model Constants

```python
GEMINI_REASONING   = "gemini-2.5-flash-lite"  # agent logic, classification, dedup
GEMINI_WRITING_MID = "gemini-2.5-flash"        # summaries, recaps, mid content
GEMINI_WRITING_PRO = "gemini-2.5-pro"          # match reports, spotlights
```

---

## Staging Environment

| Environment | URL | Database | Branch |
|------------|-----|----------|--------|
| Production | fullpitch.app | Neon main | `master` (default branch) |
| Staging | staging.fullpitch.app | Neon staging branch | `staging` |

- Neon branching is free — create `staging` branch in Neon dashboard
- Vercel auto-deploys `staging` git branch to preview URL
- Agents write to production only — never staging
- Test schema changes on staging before pushing to main

### Manual checklist — Neon (staging database branch)

1. Open [https://console.neon.tech](https://console.neon.tech) and select the **Fullpitch** (or `neondb`) project that backs production.
2. In the left nav, open **Branches** (or **Project** → **Branches**).
3. Click **Create branch**. Name it **`staging`** (or any name you prefer; keep it consistent with Vercel env below).
4. Choose a **parent**: usually **`main`** (production branch) so staging starts as a copy of current schema/data at that moment.
5. After the branch is created, open **Connection details** (or **Dashboard** → connect widget) while **branch = `staging`** is selected.
6. Copy the **pooled** PostgreSQL connection string (recommended for serverless). It must include `?sslmode=require` if Neon shows it.
7. Paste that URL into **Vercel** as `DATABASE_URL` for the staging environment only (see below). For local Prisma against staging, use the same URL in **`.env.staging.local`** as `DATABASE_URL` (this repo’s Prisma config reads `DATABASE_URL` from env files).

### Manual checklist — Vercel (git `staging` branch + env)

1. Open [https://vercel.com](https://vercel.com) → your **Fullpitch** project (the app linked to this repo).
2. **Git:** In your local repo, ensure branch **`staging`** exists and is pushed: `git push -u origin staging` (add `origin` if missing). Vercel will create a **Preview deployment** for every push to `staging` automatically once the repo is connected.
3. **Production vs preview:** Under **Settings** → **Git**, confirm the **Production Branch** is **`main`** (or **`master`** if that is what you use — align with your default branch name).
4. **Branch-specific env:** Go to **Settings** → **Environment Variables**.
5. Add **`DATABASE_URL`** (or override the existing key) with the **Neon staging branch** connection string:
   - When creating/editing the variable, set **Environment** to **Preview** only (or use **Custom branch** / “Apply to specific branches” if your Vercel UI offers it) and restrict it to the **`staging`** Git branch so production never receives the staging Neon URL.
6. Add **`NEXT_PUBLIC_SITE_ENV`** = `staging` with the same **Preview / `staging` branch** scope so the app can read staging vs production at runtime.
7. **Custom domain (optional):** **Settings** → **Domains** → add `staging.fullpitch.app` and assign it to the **staging** branch’s latest deployment (or use the preview URL Vercel assigns until DNS is ready).
8. **Redeploy:** Trigger a redeploy of the latest `staging` commit so new env vars apply (Deployments → … → Redeploy).

---

## SEO & Social Sharing

Built in from day one — not retrofitted later:
- Next.js Metadata API on every page
- Dynamic `og:image` for match results (cards shareable on X/Twitter)
- Auto-generated `sitemap.xml` from DB
- `robots.txt` properly configured
- JSON-LD structured data for matches, standings, players

---

## Mobile App (Future — React Native)

To support this from day one:
- All data endpoints at `/api/v1/`
- Consistent JSON response envelope: `{ data, meta, error }`
- Pagination on all list endpoints: `?page=1&limit=20`
- Auth via Clerk JWT — works for web and mobile
- Never return sensitive fields to unauthenticated requests

---

## Admin + Dashboard Routes

| Route | Purpose | Role |
|-------|---------|------|
| `/admin/cms/matches` | Add/edit matches | admin |
| `/admin/cms/standings` | Edit standings | admin |
| `/admin/cms/teams` | Team management | admin |
| `/admin/cms/articles` | Article management, approve drafts | admin |
| `/admin/cms/videos` | Video management | admin |
| `/admin/moderation` | Review community submissions | admin |
| `/admin/agents` | Agent logs, run history, errors | admin |
| `/dashboard` | Program rep team management | program_rep |
| `/dashboard/roster` | Manage players | program_rep |
| `/dashboard/results` | Submit match results | program_rep |
| `/dashboard/schedule` | Manage upcoming games | program_rep |

---

## Saved Assets From Old Project

These must be copied to new project before deleting old folder:

| File | What It Contains | Action |
|------|-----------------|--------|
| `prisma/schema.prisma` | 31-model validated schema | Copy to new project |
| `prisma/seed-historical.ts` | 673 matches, 3,605 player seasons | Copy to new project |
| `scripts/seedCollegeBridge.mjs` | 356 college programs, 29 conferences | Copy to new project |
| `exports/videos-backup.json` | 436 videos backed up | Copy to new project |

---

## Data Coverage Status

| Level | Teams | Quality |
|-------|-------|---------|
| MLR | 12 | ✅ Strong — historical + live |
| CRAA Men's D1A | 30 | ✅ Verified |
| CRAA Men's D1AA | ~40 | ⚠️ Partial |
| CRAA Women's | ~25 | ⚠️ Partial |
| NCR Men's D1 | 20 confirmed / 100+ total | ⚠️ Incomplete |
| NCR D2/D3 | 0 | ❌ |
| NCR Women's | 0 | ❌ |
| Club | Unknown | ⚠️ Scraper exists |
| High School | 0 | ❌ Phase 2 |
| USA Eagles | Active | ✅ |

**College data gap:** CRAA and NCR don't publish public team lists.
**Strategy:** Launch with CRAA D1A (30) + MLR (12). Grow via program rep claim system organically.

---

## Current State (May 2026)

### ✅ Complete
- Old project: 436 videos backed up to `exports/videos-backup.json`
- Old project: DB wiped — 31 tables empty and clean, schema validated
- Schema: 33 models, 16 enums — finalized app schema at `prisma/schema.prisma`; `DataConflict` added for agent conflict review and `Claim` added for program rep requests
- Historical data: ready to reseed (673 matches, 3,605 player seasons)
- Brand book + homepage mockup complete
- All architecture decisions locked
- **Step 0 (roadmap):** Next.js 15 app scaffolded in repo root (`C:\Users\josh\Desktop\Fullpitch`), Tailwind + ESLint + App Router + `src/`, shadcn/ui initialized (Slate in `components.json`, CSS variables), Prisma + Clerk + Sentry deps installed, folder skeleton + `.env.example` / `.env.local` structure
- **Step 1 (roadmap):** Prisma 7 — connection URL only in `prisma.config.ts` (`datasource.url` + `schema`; not in `schema.prisma`); dotenv loads `.env` then `.env.local` (override) for CLI; Neon `DATABASE_URL` in `.env.local`; `npx prisma validate` / `db push` / `generate` clean; **31** `public` base tables in Neon (matches 31 models); `src/lib/prisma.ts` uses `@prisma/adapter-pg` + `pg` with dev singleton on `global`
- **Step 2 (roadmap):** Clerk — `src/middleware.ts` (`/admin` → `publicMetadata.role === "admin"`; `/dashboard` → `program_rep` or `admin`; else public); `ClerkProvider` in root layout; `/sign-in` + `/sign-up` catch-alls; `src/lib/auth.ts` (`getCurrentUser`, `isAdmin`, `isProgramRep`, `requireAdmin`, `requireProgramRep`); `POST /api/webhooks/clerk` with `verifyWebhook` → upsert `User` by `clerkId`. Env: `NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY`, `CLERK_SECRET_KEY`, `CLERK_WEBHOOK_SIGNING_SECRET` (webhook) in `.env.local`.
- **Step 3 (roadmap):** Sentry — `@sentry/nextjs` with `sentry.client.config.ts`, `sentry.server.config.ts`, `sentry.edge.config.ts`, `src/instrumentation.ts` (`onRequestError`), `next.config.ts` wrapped in `withSentryConfig` (tunnel `/monitoring`; org/project/auth token from env); `src/app/global-error.tsx`; middleware matcher excludes `monitoring`. DSN only via **`NEXT_PUBLIC_SENTRY_DSN`** (no hardcoded DSN). *Wizard not run in TTY-less agent shell — layout matches official Next.js SDK; you can still run `npx @sentry/wizard@latest -i nextjs` locally to diff/merge.*
- **Step 4 (roadmap):** Vercel **Analytics** + **Speed Insights** — `npm install @vercel/analytics @vercel/speed-insights`; `<Analytics />` and `<SpeedInsights />` in `src/app/layout.tsx` after `{children}`.
- **Step 5 (roadmap):** Staging scaffold prepared but **deployment deferred** — `staging` branch guidance and **`.env.staging.local`** template exist; Neon/Vercel branch wiring intentionally postponed until pre-launch.
- **Step 6 (roadmap):** DB reset + verification complete — `npx prisma db push --force-reset` ran successfully; `scripts/verify-db.ts` confirms all **31/31** tables are reachable and all counts are **0**. DB is empty and ready for agent ingestion.
- **Step 7 (roadmap):** Tagging/source fields added to `Article`, `Video`, `Match`, `Team`, and `Player` without changing existing fields or relations; `npx prisma validate`, `db push`, `generate`, `tsc --noEmit`, and `scripts/verify-db.ts` all pass; DB remains **31/31** tables and all counts **0**.
- **Step 8 (roadmap):** Skipped intentionally — no data exists yet, so there is nothing to backfill; agents will set tags/source fields at ingest.
- **Step 12 (roadmap):** Core public REST API v1 complete — `GET /api/v1/teams`, `/teams/[id]`, `/matches`, `/matches/[id]`, `/standings`, `/players`, `/players/[id]`, `/articles`, and `/videos`; all use `{ data, meta, error }`, pagination for list endpoints, public reads only, `src/lib/prisma.ts`, and empty arrays when DB is empty. `npx tsc --noEmit` passes.
- **Step 13 (roadmap):** Agent ingest API complete — protected `POST /api/v1/ingest/match`, `/standing`, `/article`, `/video`, `/player`, and `/conflict`; all require `Authorization: Bearer {FULLPITCH_API_KEY}`, write through Prisma, tag records with `source="agent"` where supported, and log to `AgentLog`. `DataConflict` is a standalone review table. `npx prisma validate`, `db push`, `generate`, and `npx tsc --noEmit` pass.
- **Step 14 (roadmap):** Skipped intentionally — ADK agents on Railway handle all data fetching and write through the `/api/v1/ingest/` endpoints; Next.js scraper routes are not needed.
- **Step 15 (roadmap):** Skipped intentionally — Railway cron schedules the ADK agents; Vercel cron jobs are not needed.
- **Step 16 (roadmap):** Admin foundation complete — `src/app/admin/layout.tsx` guards with `requireAdmin()`, renders responsive sidebar navigation for CMS/moderation/agents, and `src/app/admin/page.tsx` shows DB record counts, pending `DataConflict` moderation count, and the last 5 `AgentLog` runs. `npx tsc --noEmit` and `npm run build` pass.
- **Step 17 (roadmap):** Admin CMS pages complete — `/admin/cms/matches`, `/standings`, `/teams`, `/articles`, and `/videos` render paginated 20-row shadcn tables, filters, sortable name/date-style headers, empty states, add forms, edit forms, and admin-protected server actions. Article status uses `publishedDate` as draft/published because the schema has no separate status field. `npx tsc --noEmit` and `npm run build` pass.
- **Step 18 (roadmap):** Admin moderation queue complete — `/admin/moderation` shows unresolved `DataConflict` rows with keep/replace actions and pending `Claim` rows with approve/reject actions. `Claim` model and `Team.claims` relation added; `User.programId` added to persist approved program access. Approving a claim sets Clerk `publicMetadata.role = "program_rep"` and `programId`, updates the local `User`, and marks the claim reviewed. `npx prisma validate`, `db push`, `generate`, `npx tsc --noEmit`, and `npm run build` pass.
- **Step 19 (roadmap):** Admin agent log viewer complete — `/admin/agents` shows paginated `AgentLog` runs with agent-name/date filters, derived finished time, ingested/skipped/error counts, expandable JSON/error details, and unresolved `DataConflict` rows using the existing moderation resolution actions. `npx tsc --noEmit` and `npm run build` pass.
- **Step 20 (roadmap):** Program rep dashboard complete — `src/app/dashboard/layout.tsx` guards with `requireProgramRep()`, looks up `User.programId` for team linkage, renders responsive sidebar (Overview, Roster, Results, Schedule, Team Info). Pages: `/dashboard` (overview with W/D/L, last 5 results, next match, roster count), `/dashboard/roster` (player list with add/edit/remove, uses Player model fields: canonicalName, firstName, lastName, position), `/dashboard/results` (submit completed match results with opponent select, home/away, scores; source="program_rep"), `/dashboard/schedule` (add upcoming games with opponent, date, location; status=SCHEDULED), `/dashboard/team` (edit team profile fields: name, shortName, abbreviation, headCoach, city, state, stadium, stadiumCapacity, logoUrl, colors, website, social handles, description; read-only competition/gender/format/level). All actions verify team ownership via `programId`. Raw SQL for Team updates to avoid Prisma enum issues. `npx tsc --noEmit` and `npm run build` pass.
- **Step 21 (roadmap):** Program claim flow complete — `/claim/[teamId]` public page (viewable by anyone, submission requires Clerk sign-in) with form fields: name, role (Head Coach / Assistant Coach / Athletic Director / Team Manager / Other), email, optional verification message. Creates `Claim` record with `status="pending"`. Pre-fills name/email from Clerk if signed in. Prevents duplicate pending claims. Success/duplicate confirmation pages. `/teams/[id]` public team page showing team info, current standing, next match, recent results, roster; "Claim This Program" button appears only when team has no approved claim. `src/lib/claims.ts` provides standalone `approveClaim(claimId, adminClerkId)` and `rejectClaim(claimId, adminClerkId, reason?)` helpers. `npx tsc --noEmit` and `npm run build` pass.
- **Agent source reliability fixes:** `FullpitchAPI` uses `httpx.Client(follow_redirects=True)` to handle 307s on all API reads/writes. Direct `httpx.get` helpers also follow redirects. `mlr_agent.py` uses no-`www` MLR URL fallback chains for scores/schedule/results/games and standings/table/league-table, logging the URL that succeeds. `wer_agent.py` uses the official WER domain `https://www.womenseliterugby.us` with `/2026-schedule` and `/standings` first, with older domains as fallbacks. `python -m compileall tools agents` passes.

### ⚠️ In Progress
- Saving remaining assets from old project (seeds, `videos-backup.json`) as needed

### ❌ Not Started
- Phase 1 seed scripts (`seed-historical`, college bridge, video restore)
- All scrapers, agents, full CMS — everything else

---

## Immediate Next Steps

1. Set **Sentry** env in `.env.local`: `NEXT_PUBLIC_SENTRY_DSN`, optional `SENTRY_AUTH_TOKEN` + `SENTRY_ORG` + `SENTRY_PROJECT` for production source maps.
2. Paste Clerk keys / webhook secret if not already (Step 2).
3. Proceed to **Step 22** per roadmap.
4. Revisit full staging branch + Neon branch setup at pre-launch.

---

## Decisions Log

| Date | Decision |
|------|----------|
| May 2026 | 100% fresh start — new project folder, clean codebase, delete old repo |
| May 2026 | ADK Python on Railway — locked forever, never TypeScript, never Workers |
| May 2026 | Gemini 2.5 models only — flash-lite (reasoning), flash (mid), pro (quality) |
| May 2026 | Gemini 2.0 Flash deprecated June 1 2026 — never reference it |
| May 2026 | Auth: Clerk — 3 roles: admin, program_rep, user |
| May 2026 | Program rep system: Option C hybrid — community submits, reps self-manage |
| May 2026 | Staging: Vercel preview + Neon DB branch — free |
| May 2026 | Mobile app planned — all API routes /api/v1/ from day one |
| May 2026 | Content agent: AI writes match reports (2.5-pro), summaries (2.5-flash) |
| May 2026 | Prompts in versioned .txt files at fullpitch-agents/prompts/ — never hardcoded |
| May 2026 | Analytics: Vercel Analytics (Pro) |
| May 2026 | Sentry free tier — DSN in env vars, not hardcoded (`NEXT_PUBLIC_SENTRY_DSN`); build-time upload via `SENTRY_AUTH_TOKEN` / `SENTRY_ORG` / `SENTRY_PROJECT` when set |
| May 2026 | Error tracking: Sentry on app; agents remain separate DSN (Railway) per plan |
| May 2026 | Vercel Analytics + Speed Insights in root layout; staging git branch + `.env.staging.local` reference; Neon branch + Vercel Preview-scoped `DATABASE_URL` for staging |
| May 2026 | Staging deployment intentionally deferred until pre-launch; analytics shipped now, staging env wiring revisited at launch readiness |
| May 2026 | DB hard reset approved and executed (`prisma db push --force-reset`); verification script confirms 31 tables reachable and empty (all zero counts) |
| May 2026 | Tagging/source fields added additively; existing enum/string-list fields with the same names were preserved to avoid breaking schema semantics |
| May 2026 | Backfill tag step skipped because DB is empty; agents will tag/source every record at ingest |
| May 2026 | Public REST API v1 reads are versioned under `/api/v1`, use the standard `{ data, meta, error }` envelope, and are intentionally unauthenticated |
| May 2026 | Agent ingest API is protected by `FULLPITCH_API_KEY`, uses idempotent writes where supported, writes `AgentLog`, and records admin-review conflicts in `DataConflict` |
| May 2026 | Steps 14 and 15 skipped — ADK agents on Railway handle fetching and cron scheduling; Vercel scraper routes/crons are not needed |
| May 2026 | Step 16 admin foundation added with `requireAdmin()` layout guard, responsive CMS navigation, dashboard counts, pending conflict count, and recent agent runs |
| May 2026 | Step 17 admin CMS pages added for matches, standings, teams, articles, and videos with shadcn tables/forms, pagination, filters, sortable headers, empty states, and admin-guarded server actions |
| May 2026 | Step 18 moderation queue added with DataConflict resolution, program Claim review, Clerk role promotion to `program_rep`, and local `User.programId` persistence |
| May 2026 | Step 19 agent log viewer added at `/admin/agents` with filterable/paginated `AgentLog` runs, expandable details, and reused DataConflict resolution actions |
| May 2026 | UI: Tailwind + shadcn/ui, mobile-first always |
| May 2026 | SEO + og:image built from day one — not retrofitted |
| May 2026 | College gap: launch CRAA D1A + MLR, grow via program rep claims |
| May 2026 | App repo path: `C:\Users\josh\Desktop\Fullpitch` — Next.js 15 scaffold in folder root (no subfolder); npm package name `fullpitch` |
| May 2026 | Prisma 7 — URL in `prisma.config.ts`, adapter-pg pattern (`datasource.url` in config; Prisma 7.8 has no `datasourceUrl` key — `db push` requires `datasource.url`). No URL in `schema.prisma`. |
| May 2026 | Agent HTTP fixes: API client and direct `httpx.get` calls follow redirects; MLR and WER source URL fallback chains updated after 307/404/DNS failures. |
| May 2026 | Clerk — roles in `publicMetadata.role` (`admin` \| `program_rep` \| `user`); route gating in `clerkMiddleware` + `clerkClient.users.getUser`; webhooks via `verifyWebhook` + `CLERK_WEBHOOK_SIGNING_SECRET` |
| Apr 2026 | Brand book and homepage mockup finalized |

---

*Last updated: May 2026 — Josh Russo*
