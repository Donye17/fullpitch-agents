"""Boss Agent — orchestrates all sub-agents.

Runs on Railway cron. Delegates to sub-agents based on schedule and priority.

Run order:
  1. mlr_agent         — MLR scores + standings
  2. wer_agent         — Women's Elite Rugby scores + standings
  3. news_agent        — article ingest + relevance filter
  4. video_agent       — YouTube content discovery
  5. eagles_agent      — USA Eagles results
  6. college_agent     — college scores + standings
  7. hs_agent          — high school tournament diagnostics
  8. craa_agent        — CRAA news, rankings, postseason results
  9. ncr_agent         — NCR news and results
 10. nira_agent        — NIRA news and results
 11. maintenance_agent — article metadata repair
"""

import logging
import time

logger = logging.getLogger(__name__)

AGENT_RUN_ORDER = [
    ("mlr", "agents.mlr_agent"),
    ("wer", "agents.wer_agent"),
    ("news", "agents.news_agent"),
    ("video", "agents.video_agent"),
    ("eagles", "agents.eagles_agent"),
    ("college", "agents.college_agent"),
    ("hs", "agents.hs_agent"),
    ("craa", "agents.craa_agent"),
    ("ncr", "agents.ncr_agent"),
    ("nira", "agents.nira_agent"),
    ("maintenance", "agents.maintenance_agent"),
]


def run() -> None:
    logger.info("Boss agent started — running %d sub-agents", len(AGENT_RUN_ORDER))
    results: dict[str, str] = {}

    for name, module_path in AGENT_RUN_ORDER:
        logger.info("Running sub-agent: %s", name)
        start = time.time()
        try:
            module = __import__(module_path, fromlist=["run"])
            run_fn = getattr(module, "run", None)
            if run_fn is None:
                logger.error("Agent module %s has no run() function", module_path)
                results[name] = "ERROR: no run()"
                continue
            run_fn()
            elapsed = time.time() - start
            results[name] = f"OK ({elapsed:.1f}s)"
            logger.info("Sub-agent %s completed in %.1fs", name, elapsed)
        except Exception:
            elapsed = time.time() - start
            results[name] = f"FAILED ({elapsed:.1f}s)"
            logger.exception("Sub-agent %s failed after %.1fs", name, elapsed)

    logger.info("Boss agent complete — results: %s", results)
