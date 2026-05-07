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
| Reddit | ✅ Easy | Free, no auth needed | Free | ✅ Add to news agent |
| LinkedIn | Blocked | Limited | — | Skip |

---

## Reddit — Add to News Agent Now

Reddit is the best free social source for US rugby. Active communities post
match results, signings, injuries, and discussion in real time — often before
official sites update.

### Subreddits to Monitor

| Subreddit | What's There |
|-----------|-------------|
| r/MLRugby | MLR match threads, results, news, signings |
| r/rugbyunion | General rugby — filter for US content |
| r/rugbyunion_chat | Discussion — filter for US content |
| r/collegiaterugby | College rugby results and discussion |
| r/usarugby | USA Eagles, national team news |

### How to Hit the Reddit API (Free, No Auth)

```python
import httpx

def fetch_subreddit_new(subreddit: str, limit: int = 25):
    url = f"https://www.reddit.com/r/{subreddit}/new.json?limit={limit}"
    headers = {"User-Agent": "FullpitchBot/1.0"}
    response = httpx.get(url, headers=headers, timeout=15)
    return response.json()["data"]["children"]

def search_reddit(subreddit: str, query: str, limit: int = 10):
    url = f"https://www.reddit.com/r/{subreddit}/search.json"
    params = {"q": query, "sort": "new", "limit": limit, "restrict_sr": "true"}
    headers = {"User-Agent": "FullpitchBot/1.0"}
    response = httpx.get(url, params=params, headers=headers, timeout=15)
    return response.json()["data"]["children"]
```

### What to Do With Reddit Posts

1. Fetch new posts from each subreddit
2. Use Gemini (GEMINI_REASONING) to classify:
   "Is this post about a US rugby match result, signing,
   injury, or significant news? YES or NO only."
3. If YES → extract key info and write as Article:
   - title = post title
   - url = reddit post URL
   - source = "reddit"
   - league = classify from content
4. Skip: memes, general discussion, non-US content, reposts

### Rate Limiting

Reddit allows ~60 requests/minute unauthenticated.
Add 1 second delay between subreddit fetches.
Always send User-Agent: FullpitchBot/1.0

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
| Reddit r/MLRugby, r/usarugby | ✅ Now | news_agent.py |
| Reddit r/collegiaterugby | ✅ Now | college_agent.py |
| YouTube | ✅ Already planned | video_agent.py |
| Google Search grounding | ⚠️ Selectively | Any agent, sparingly |
| X/Twitter API | ❌ Phase 2 | social_agent.py |

---

## Notes for news_agent.py

Add Reddit alongside web sources:

```python
SOURCES = [
    # Web
    {"url": "mlrugby.com/news", "type": "scrape", "league": "mlr"},
    {"url": "usa.rugby/news", "type": "scrape", "league": "eagles"},
    {"url": "rugbypass.com", "type": "scrape", "filter": True},
    {"url": "ultimaterugby.com", "type": "scrape", "filter": True},

    # Reddit
    {"subreddit": "MLRugby", "type": "reddit", "league": "mlr"},
    {"subreddit": "usarugby", "type": "reddit", "league": "eagles"},
    {"subreddit": "rugbyunion", "type": "reddit", "filter": True},
    {"subreddit": "collegiaterugby", "type": "reddit", "league": "college"},
]
```

---

*Last updated: May 2026*
*Read when building news_agent.py and video_agent.py*
