"""
GuardAI — Improved Flask Backend
=================================
Replaces the old /analyze-news endpoint with a much smarter pipeline:

1. Google Custom Search API  — searches the live web for the exact claim
2. NewsAPI / GNews fallback  — existing news APIs for corroboration count
3. ClaimBuster / Google Fact Check Tools API — dedicated fact-check lookup
4. Semantic similarity        — fuzzy title matching via difflib (no heavy ML needed)
5. Weighted scoring           — combines all signals into a final verdict

HOW TO SET UP:
--------------
pip install flask flask-cors requests

Get FREE API keys:
  - Google Custom Search: https://developers.google.com/custom-search/v1/introduction
    (100 free queries/day)
  - Google Programmable Search Engine ID: https://programmablesearchengine.google.com/
    (create one, set to "Search the entire web")
  - Google Fact Check Tools API: same Google Cloud project, enable "Fact Check Tools API"
    (free, 1000 queries/day)
  - Optional: SerpAPI (serpapi.com) — 100 free/month, more reliable

Set your keys in the CONFIG section below or via environment variables.
"""

import os, re, json, math, requests

# REPLACE with this one line:
from flask import Flask, request, jsonify, render_template
from flask_cors import CORS
from difflib import SequenceMatcher
from urllib.parse import quote_plus

app = Flask(__name__)
CORS(app)

# ═══════════════════════════════════════════════════════════════
# CONFIG — fill these in or set as environment variables
# ═══════════════════════════════════════════════════════════════
GOOGLE_API_KEY       = os.getenv("GOOGLE_API_KEY", "AQ.Ab8RN6IPLGaOg5iPAPmoEvzmTRcOrmDA5iS4rMP91Vnd_Hp6CA")
GOOGLE_CSE_ID        = os.getenv("GOOGLE_CSE_ID", "16d5f85dff0c440a1")      # Programmable Search Engine ID
FACT_CHECK_API_KEY   = os.getenv("FACT_CHECK_API_KEY", GOOGLE_API_KEY)      # Same key, different API
NEWSDATA_API_KEY     = os.getenv("NEWSDATA_API_KEY", "pub_ac173e168fcf4f398a2bfdba4cb42fd5")
GNEWS_API_KEY        = os.getenv("GNEWS_API_KEY", "a5f8bf0087f6b5f211e06dd422d32eb0")
SERPAPI_KEY          = os.getenv("SERPAPI_KEY", "")                          # Optional, more reliable

# Trusted news domains — presence of these in results boosts real scorey
TRUSTED_DOMAINS = {
    "thehindu.com", "bbc.com", "bbc.co.uk", "reuters.com", "apnews.com",
    "ndtv.com", "theindianexpress.com", "hindustantimes.com", "livemint.com",
    "timesofindia.com", "economictimes.com", "theguardian.com", "nytimes.com",
    "washingtonpost.com", "aljazeera.com", "cnn.com", "abc.net.au",
    "theprint.in", "scroll.in", "thewire.in", "news18.com", "indiatoday.in",
}

# Known misinformation / satire domains
FAKE_DOMAINS = {
    "theonion.com", "babylonbee.com", "nationalreport.net",
    "worldnewsdailyreport.com", "empirenews.net",
}

# ═══════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════

def similarity(a: str, b: str) -> float:
    """Case-insensitive fuzzy string similarity 0-1."""
    return SequenceMatcher(None, a.lower().strip(), b.lower().strip()).ratio()


def extract_domain(url: str) -> str:
    """Pull bare domain from a URL."""
    m = re.search(r'https?://(?:www\.)?([^/]+)', url or '')
    return m.group(1).lower() if m else ''


def normalize_claim(text: str) -> str:
    """Clean up the claim for searching."""
    text = text.strip().strip('"\'')
    # Remove leading "breaking:", "report:", etc.
    text = re.sub(r'^(breaking|report|exclusive|update)\s*:\s*', '', text, flags=re.I)
    return text

# ═══════════════════════════════════════════════════════════════
# SOURCE 1 — Google Custom Search (or SerpAPI fallback)
# ═══════════════════════════════════════════════════════════════

def google_search(query: str, num: int = 10) -> list[dict]:
    """
    Returns list of {title, url, snippet, domain} from Google Search.
    Tries SerpAPI first if key is set, falls back to Custom Search JSON API.
    """
    results = []

    # ── SerpAPI (more reliable, 100 free/month) ──
    if SERPAPI_KEY and SERPAPI_KEY != "":
        try:
            r = requests.get(
                "https://serpapi.com/search",
                params={"q": query, "num": num, "api_key": SERPAPI_KEY, "engine": "google"},
                timeout=8
            )
            if r.ok:
                for item in r.json().get("organic_results", []):
                    results.append({
                        "title":   item.get("title", ""),
                        "url":     item.get("link", ""),
                        "snippet": item.get("snippet", ""),
                        "domain":  extract_domain(item.get("link", ""))
                    })
                return results
        except Exception:
            pass

    # ── Google Custom Search JSON API ──
    if GOOGLE_API_KEY and GOOGLE_API_KEY != "YOUR_GOOGLE_API_KEY_HERE":
        try:
            r = requests.get(
                "https://www.googleapis.com/customsearch/v1",
                params={
                    "key": GOOGLE_API_KEY,
                    "cx":  GOOGLE_CSE_ID,
                    "q":   query,
                    "num": min(num, 10)
                },
                timeout=8
            )
            if r.ok:
                for item in r.json().get("items", []):
                    results.append({
                        "title":   item.get("title", ""),
                        "url":     item.get("link", ""),
                        "snippet": item.get("snippet", ""),
                        "domain":  extract_domain(item.get("link", ""))
                    })
                return results
        except Exception:
            pass

    return results


# ═══════════════════════════════════════════════════════════════
# SOURCE 2 — Google Fact Check Tools API
# ═══════════════════════════════════════════════════════════════

def fact_check_lookup(query: str) -> dict:
    """
    Returns {found: bool, rating: str, publisher: str, url: str} or {found: False}
    """
    if not FACT_CHECK_API_KEY or FACT_CHECK_API_KEY == "YOUR_GOOGLE_API_KEY_HERE":
        return {"found": False}
    try:
        r = requests.get(
            "https://factchecktools.googleapis.com/v1alpha1/claims:search",
            params={"key": FACT_CHECK_API_KEY, "query": query, "languageCode": "en"},
            timeout=6
        )
        if r.ok:
            claims = r.json().get("claims", [])
            if claims:
                top = claims[0]
                reviews = top.get("claimReview", [{}])
                rev = reviews[0] if reviews else {}
                return {
                    "found":     True,
                    "claim":     top.get("text", ""),
                    "rating":    rev.get("textualRating", ""),
                    "publisher": rev.get("publisher", {}).get("name", ""),
                    "url":       rev.get("url", ""),
                }
    except Exception:
        pass
    return {"found": False}


# ═══════════════════════════════════════════════════════════════
# SOURCE 3 — NewsData.io (existing)
# ═══════════════════════════════════════════════════════════════

def newsdata_search(query: str) -> list[dict]:
    if not NEWSDATA_API_KEY:
        return []
    try:
        r = requests.get(
            "https://newsdata.io/api/1/news",
            params={"apikey": NEWSDATA_API_KEY, "q": query, "language": "en"},
            timeout=6
        )
        articles = []
        for a in r.json().get("results", []):
            articles.append({
                "title":  a.get("title", ""),
                "url":    a.get("link", ""),
                "domain": extract_domain(a.get("link", ""))
            })
        return articles
    except Exception:
        return []


# ═══════════════════════════════════════════════════════════════
# SOURCE 4 — GNews (existing)
# ═══════════════════════════════════════════════════════════════

def gnews_search(query: str) -> list[dict]:
    if not GNEWS_API_KEY:
        return []
    try:
        r = requests.get(
            "https://gnews.io/api/v4/search",
            params={"q": query, "token": GNEWS_API_KEY, "lang": "en", "max": 10},
            timeout=6
        )
        articles = []
        for a in r.json().get("articles", []):
            articles.append({
                "title":  a.get("title", ""),
                "url":    a.get("url", ""),
                "domain": extract_domain(a.get("url", ""))
            })
        return articles
    except Exception:
        return []


# ═══════════════════════════════════════════════════════════════
# SCORING ENGINE
# ═══════════════════════════════════════════════════════════════

def score_results(claim: str, google_results: list, news_results: list, fact_check: dict) -> dict:
    """
    Combines all signals into a weighted verdict.

    Signal weights (total 100):
      - Fact check found + real:    +40
      - Fact check found + fake:    -40
      - Trusted domain in Google:   +25 (per domain, max 50)
      - Fake domain in Google:      -30
      - High similarity Google hit: +20
      - News API corroboration:     +15 (proportional)
      - Zero evidence anywhere:     -20
    """
    score = 50  # neutral starting point
    details = []

    # ── Fact check signal ──
    fc_label = "No fact-check found"
    if fact_check.get("found"):
        rating = fact_check.get("rating", "").upper()
        pub    = fact_check.get("publisher", "Unknown")
        fc_label = f'{rating} — {pub}'
        if any(w in rating for w in ["TRUE", "CORRECT", "ACCURATE", "REAL", "VERIFIED"]):
            score += 40
            details.append(f"✔ Fact-checked as TRUE by {pub}")
        elif any(w in rating for w in ["FALSE", "FAKE", "MISLEAD", "WRONG", "INCORRECT", "PANTS"]):
            score -= 40
            details.append(f"✘ Fact-checked as FALSE by {pub}")
        else:
            details.append(f"~ Fact-check found but rating ambiguous: {rating}")

    # ── Google Search signal ──
    trusted_found = set()
    fake_found    = set()
    best_sim      = 0.0
    best_match    = ""

    for item in google_results:
        dom  = item.get("domain", "")
        title = item.get("title", "")
        snippet = item.get("snippet", "")

        # Check domain trust
        if dom in TRUSTED_DOMAINS:
            trusted_found.add(dom)
        if dom in FAKE_DOMAINS:
            fake_found.add(dom)

        # Semantic similarity of title to claim
        sim = max(similarity(claim, title), similarity(claim, snippet[:200]))
        if sim > best_sim:
            best_sim = sim
            best_match = title

    # Apply trusted domain bonus (capped)
    trust_bonus = min(len(trusted_found) * 25, 50)
    if trusted_found:
        score += trust_bonus
        details.append(f"✔ Found on {len(trusted_found)} trusted source(s): {', '.join(list(trusted_found)[:3])}")

    if fake_found:
        score -= 30
        details.append(f"✘ Found on known satire/fake domain: {', '.join(fake_found)}")

    # Similarity bonus
    if best_sim >= 0.65:
        score += 20
        details.append(f"✔ High similarity match ({int(best_sim*100)}%): \"{best_match[:80]}\"")
    elif best_sim >= 0.40:
        score += 10
        details.append(f"~ Partial match ({int(best_sim*100)}%): \"{best_match[:80]}\"")
    elif google_results:
        details.append(f"~ Low similarity ({int(best_sim*100)}%) — topic found but titles differ")

    # ── News API corroboration ──
    news_trusted = sum(1 for a in news_results if a.get("domain","") in TRUSTED_DOMAINS)
    news_sim_scores = [similarity(claim, a.get("title","")) for a in news_results]
    avg_news_sim = sum(news_sim_scores) / len(news_sim_scores) if news_sim_scores else 0

    if len(news_results) >= 3:
        score += 10
        details.append(f"✔ {len(news_results)} news articles found via APIs")
    elif len(news_results) >= 1:
        score += 5

    if news_trusted:
        score += 5
        details.append(f"✔ {news_trusted} trusted source(s) in news APIs")

    # ── Zero evidence penalty ──
    if not google_results and not news_results and not fact_check.get("found"):
        score -= 20
        details.append("✘ No corroborating evidence found anywhere")

    # ── Clamp to 0-100 ──
    score = max(0, min(100, score))

    # ── Verdict label ──
    if score >= 70:
        label = "REAL"
        verdict = "Claim Verified"
    elif score >= 45:
        label = "UNCERTAIN"
        verdict = "Insufficient Evidence"
    else:
        label = "FAKE"
        verdict = "Claim Disputed"

    return {
        "label":      label,
        "verdict":    verdict,
        "confidence": round(score),
        "fc_label":   fc_label,
        "details":    details,
        "evidence": {
            "articles_found":   len(news_results),
            "google_results":   len(google_results),
            "trusted_sources":  list(trusted_found),
            "google_trust":     len(trusted_found) > 0,
            "google_match":     round(best_sim, 2),
            "agreement":        round(avg_news_sim, 2),
            "fact_check":       fact_check,
        }
    }


# ═══════════════════════════════════════════════════════════════
# FLASK ROUTES
# ═══════════════════════════════════════════════════════════════

@app.route("/analyze-news", methods=["POST"])
def analyze_news():
    data  = request.get_json(force=True)
    query = data.get("query", "").strip()
    if not query:
        return jsonify({"error": "No query provided"}), 400

    claim = normalize_claim(query)

    # Run all sources in parallel would be ideal; for simplicity run sequentially
    google_results = google_search(claim, num=10)
    fact_check     = fact_check_lookup(claim)
    news_results   = newsdata_search(claim) + gnews_search(claim)

    result = score_results(claim, google_results, news_results, fact_check)
    result["query"] = query
    return jsonify(result)


@app.route("/upload", methods=["POST"])
def upload():
    """Image analysis — keep your existing deepfake model logic here."""
    # ── Paste your existing /upload route code below ──
    # This is a placeholder that returns a mock response.
    # Replace with your actual ResNet-50 / deepfake detection code.
    import random
    confidence = random.randint(60, 99)
    label      = "REAL" if confidence > 70 else "FAKE"
    return jsonify({"label": label, "confidence": confidence})

@app.route("/")
def index():
    return render_template('index.html')

if __name__ == "__main__":
    print("\n" + "="*60)
    print("  GuardAI Backend — Improved Scoring Engine")
    print("="*60)
    if GOOGLE_API_KEY == "YOUR_GOOGLE_API_KEY_HERE":
        print("  ⚠  GOOGLE_API_KEY not set — Google Search disabled")
        print("     Set it: export GOOGLE_API_KEY=your_key_here")
    else:
        print(f"  ✔  Google CSE: {GOOGLE_CSE_ID[:12]}...")
    if SERPAPI_KEY:
        print(f"  ✔  SerpAPI key set")
    print("="*60 + "\n")
    app.run(debug=True, port=5000)