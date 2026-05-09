# FULLPITCH — Social & Community Data Sources

> Read this when building the news agent and video agent.
> Drop this file in fullpitch-agents/ root.

---

## Platform Reality Check

| Platform | Scraping | API | Cost | Use It? |
|----------|---------|-----|------|---------|
| X/Twitter | Blocked | Yes — Basic tier | $100/month | Phase 2 only |
| Instagram | Blocked | No useful API | — | Skip |
| Facebook | Blocked | Very limited | — | Skip |
| YouTube | ✅ Easy | YouTube Data API v3 | Free tier | ✅ Already planned |
| Reddit | Blocked/unreliable for bots | Free but fragile | Free | Skip |
| LinkedIn | Blocked | Limited | — | Skip |

---

## Reddit — Do Not Scrape

Do not ingest Reddit posts as news. Reddit blocks bots unpredictably and the
content is community discussion, not official rugby news. `news_agent.py` should
only ingest official/news-site article URLs that pass the shared article URL
filter.

---

## Google Search Grounding — Indirect Social Access

When Gemini has Google Search grounding enabled it can find:
- Tweets indexed by Google
- Instagram posts indexed by Google
- Any public social content

Gets social data without fighting platform blocks.

Cost: $14 per 1,000 searches (Gemini API with grounding)
Use for: One-off lookups, not bulk scraping
Not for bulk ingestion — too expensive at scale.

---

## X/Twitter — Phase 2 Only

When Fullpitch has traction and needs real-time score updates:

- X Basic API: $100/month
- Use case: social_agent.py monitors official MLR team accounts
  for score posts, ingests results in near real-time

### MLR Team X Accounts to Monitor (when ready)

```python
MLR_TEAM_ACCOUNTS = [
    "chicagohounds",
    "nolarebels",
    "rugbyatl",
    "rugbyutah",
    "seattleseawolves",
    "sdlegion",
    "nefreejacks",
    "houstonsbr",
    "oldgloryrugby",
    "rugbylouisville",
    "miamisharkrugby",
    "portlandrugbyclub",
]
```

Build social_agent.py in Phase 2 when justified.

---

## Implementation Priority

| Source | Priority | Agent |
|--------|---------|-------|
| YouTube | ✅ Already planned | video_agent.py |
| Google Search grounding | ⚠️ Selectively | Any agent, sparingly |
| X/Twitter API | ❌ Phase 2 | social_agent.py |

---

## Notes for news_agent.py

Use official article sources only:

```python
SOURCES = [
    {"url": "majorleague.rugby/news", "type": "scrape", "league": "mlr"},
    {"url": "usa.rugby/news", "type": "scrape", "league": "eagles"},
    {"url": "rugbypass.com", "type": "scrape", "filter": True},
    {"url": "ultimaterugby.com", "type": "scrape", "filter": True},
]
```

---

*Last updated: May 2026*
*Read when building news_agent.py and video_agent.py*
