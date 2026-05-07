# FILE: ai_briefing.py
# Bloomberg Macro Terminal — AI Morning Briefing via Anthropic

import os
import json
import logging
from datetime import datetime, timedelta

log = logging.getLogger(__name__)

CACHE_FILE  = "briefing_cache.json"
CACHE_HOURS = 6


def get_briefing():
    """Generate AI morning briefing from current dashboard data."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return {
            "status": "no_api_key",
            "message": "Anthropic API key not configured",
            "setup_instructions": [
                "1. Go to console.anthropic.com",
                "2. Create account separate from Claude Pro",
                "3. Add credit card (you'll be billed pennies per month)",
                "4. Set spending limit to $5/month for safety",
                "5. Create API key (starts with sk-ant-)",
                "6. In Replit: Tools > Secrets > add ANTHROPIC_API_KEY",
                "7. Refresh this page"
            ]
        }

    cached = _load_cache()
    if cached and _cache_valid(cached):
        return {
            "status":       "success",
            "briefing":     cached["briefing"],
            "generated_at": cached["generated_at"],
            "from_cache":   True,
        }

    try:
        import requests
        base = "http://localhost:5000"
        regime_data = requests.get(f"{base}/api/regime", timeout=10).json()
        macro_data  = requests.get(f"{base}/api/macro",  timeout=10).json()
        yields_data = requests.get(f"{base}/api/yields", timeout=10).json()
        credit_data = requests.get(f"{base}/api/credit", timeout=10).json()
        market_data = requests.get(f"{base}/api/market", timeout=10).json()
        news_data   = requests.get(f"{base}/api/news",   timeout=10).json()
        top_news = (news_data.get("articles") or [])[:3]
    except Exception as e:
        return {"status": "data_error", "message": f"Could not fetch dashboard data: {e}"}

    context = {
        "current_date":     datetime.now().strftime("%A, %B %d %Y"),
        "current_regime":   regime_data.get("label") or regime_data.get("regime", "unknown"),
        "regime_confidence": regime_data.get("confidence_score") or regime_data.get("confidence", 0),
        "macro_indicators": macro_data,
        "yield_curve":      yields_data,
        "credit_spreads":   credit_data,
        "market_data":      market_data,
        "top_headlines":    [
            {
                "title":   a.get("title", ""),
                "source":  a.get("source", ""),
                "summary": (a.get("description") or "")[:200],
            } for a in top_news
        ],
    }

    try:
        from anthropic import Anthropic
        client = Anthropic(api_key=api_key)

        system_prompt = (
            "You are a macro analyst briefing financial advisor Ryan Chapman. "
            "He has held a stagflation thesis since 2021. His bear case is private credit "
            "cascade leading to wealth effect reversal. His bull case fear is Fed cuts "
            "aggressively and goldilocks resumes.\n\n"
            "Generate a concise morning briefing under 300 words with these sections:\n\n"
            "OVERNIGHT / RECENT CHANGES — What moved, what mattered\n\n"
            "THESIS STATUS — Is stagflation thesis strengthening, stable, or weakening "
            "based on the data? Be specific about which indicators support this view.\n\n"
            "WATCH TODAY — What specific data points or events to watch\n\n"
            "ONE RISK YOU MAY BE MISSING — Counter-thesis perspective. What would "
            "challenge Ryan's view that he should consider?\n\n"
            "Be direct, specific, actionable. Reference exact data points. No fluff. "
            "Use plain English not jargon. Format with clear section headers."
        )

        user_message = (
            f"Generate today's macro briefing based on this current data:\n\n"
            f"Date: {context['current_date']}\n\n"
            f"CURRENT REGIME:\n{context['current_regime']} (confidence: {context['regime_confidence']}%)\n\n"
            f"MACRO INDICATORS:\n{json.dumps(context['macro_indicators'], indent=2)}\n\n"
            f"YIELD CURVE:\n{json.dumps(context['yield_curve'], indent=2)}\n\n"
            f"CREDIT SPREADS:\n{json.dumps(context['credit_spreads'], indent=2)}\n\n"
            f"MARKET DATA:\n{json.dumps(context['market_data'], indent=2)}\n\n"
            f"TOP HEADLINES:\n{json.dumps(context['top_headlines'], indent=2)}"
        )

        message = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=600,
            system=system_prompt,
            messages=[{"role": "user", "content": user_message}],
        )

        briefing_text = message.content[0].text
        _save_cache(briefing_text)

        return {
            "status":       "success",
            "briefing":     briefing_text,
            "generated_at": datetime.now().isoformat(),
            "from_cache":   False,
        }

    except Exception as e:
        log.error(f"AI briefing error: {e}")
        return {
            "status":   "api_error",
            "message":  f"AI briefing failed: {e}",
            "fallback": "Briefing temporarily unavailable. Check API key and try again.",
        }


def force_regenerate():
    """Force regeneration ignoring cache."""
    try:
        os.remove(CACHE_FILE)
    except FileNotFoundError:
        pass
    return get_briefing()


def _load_cache():
    try:
        with open(CACHE_FILE, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def _save_cache(briefing_text):
    with open(CACHE_FILE, "w") as f:
        json.dump({"briefing": briefing_text, "generated_at": datetime.now().isoformat()}, f)


def _cache_valid(cached):
    if not cached or "generated_at" not in cached:
        return False
    age = datetime.now() - datetime.fromisoformat(cached["generated_at"])
    return age < timedelta(hours=CACHE_HOURS)
