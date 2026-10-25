"""Configuration for CogniSync."""

import os
from dotenv import load_dotenv

load_dotenv()

# OpenRouter API key
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")

# ---------------------------------------------------------------------------
# Free-testing mode
# ---------------------------------------------------------------------------
# When enabled (default), the council only uses models the OpenRouter catalog
# reports as free (zero prompt/completion pricing, preferably ':free' ids).
# Requests for paid models are rejected instead of silently billed.
FREE_TESTING_MODE = os.getenv("FREE_TESTING_MODE", "true").lower() not in ("0", "false", "no")

# Number of council members to assemble when no explicit selection is given.
COUNCIL_MODEL_COUNT = int(os.getenv("COUNCIL_MODEL_COUNT", "4"))

# ponytail: fallback ids are NOT guaranteed — they are documented historical
# ':free' variants used only when the live catalog is unreachable. Every
# resolve pass must validate them against a live catalog whenever it is
# reachable (see model_catalog.is_free_testing_eligible) and swap them out
# if OpenRouter delisted them.
FALLBACK_FREE_MODEL_IDS = [
    "deepseek/deepseek-chat-v3.1:free",
    "meta-llama/llama-3.3-70b-instruct:free",
    "google/gemma-3-27b-it:free",
    "qwen/qwen3-14b:free",
]

# Backwards-compatible council list: used by council orchestration only when
# callers do not supply resolved models. Defaults to the fallback free list.
COUNCIL_MODELS = list(FALLBACK_FREE_MODEL_IDS)

# Chairman defaults to the first fallback free model; callers may resolve a
# catalog-verified chairman instead.
CHAIRMAN_MODEL = os.getenv("CHAIRMAN_MODEL", FALLBACK_FREE_MODEL_IDS[0])

# OpenRouter endpoints
OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"
MODEL_CATALOG_URL = "https://openrouter.ai/api/v1/models"

# Catalog cache lifetime in seconds (UI refresh triggers a force update).
MODEL_CATALOG_TTL_SECONDS = int(os.getenv("MODEL_CATALOG_TTL_SECONDS", "600"))

# --- Rate-limit / request policy -------------------------------------------
# Free-tier OpenRouter accounts get a small per-account daily quota (e.g. 50
# free-model requests/day). 429s carrying a daily/quota marker are terminal;
# transient 429s are retried with bounded backoff.
OPENROUTER_MAX_CONCURRENCY = int(os.getenv("OPENROUTER_MAX_CONCURRENCY", "5"))
RATE_LIMIT_MAX_ATTEMPTS = int(os.getenv("RATE_LIMIT_MAX_ATTEMPTS", "3"))          # total attempts incl. first
RATE_LIMIT_BACKOFF_CAP_SECONDS = float(os.getenv("RATE_LIMIT_BACKOFF_CAP_SECONDS", "30"))
RATE_LIMIT_TOTAL_TIMEOUT = float(os.getenv("RATE_LIMIT_TOTAL_TIMEOUT", "90"))     # whole-request budget

# Data directory for conversation storage
DATA_DIR = "data/conversations"

# ---------------------------------------------------------------------------
# DuckDuckGo Search (web search integration)
# ---------------------------------------------------------------------------
# Library-based search via the maintained `ddgs` package. No API key is
# required. Queries run server-side only; nothing is exposed to the browser.
DDG_MAX_RESULTS = int(os.getenv("DDG_MAX_RESULTS", "8"))
DDG_TIMEOUT = float(os.getenv("DDG_TIMEOUT", "20"))

# ---------------------------------------------------------------------------
# GitHub integration (direct official REST API, read-only)
# ---------------------------------------------------------------------------
# Personal access token with repo read scope (public data works unauthenticated
# at reduced rate limits). Never exposed to the frontend or logs.
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
GITHUB_API_URL = "https://api.github.com"
GITHUB_TIMEOUT = 15.0

# ---------------------------------------------------------------------------
# Notion integration (direct official API)
# ---------------------------------------------------------------------------
# Internal integration token (secret_...) with pages granted in Notion.
# Never exposed to the frontend or logs.
NOTION_TOKEN = os.getenv("NOTION_TOKEN")
NOTION_API_URL = "https://api.notion.com/v1"
NOTION_API_VERSION = "2022-06-28"
NOTION_TIMEOUT = 15.0

# Connection test timeout for integration checks.
TOOLS_TEST_TIMEOUT = 15.0
