from flask import Flask, render_template, request, jsonify
import tensorflow as tf
import numpy as np
from tensorflow.keras.preprocessing import image
from tensorflow.keras.applications.efficientnet import preprocess_input
import os
import joblib
import requests
import urllib3
from sentence_transformers import SentenceTransformer
from sklearn.metrics.pairwise import cosine_similarity
from datetime import datetime, timezone
from dotenv import load_dotenv

load_dotenv("keys.env")

app = Flask(__name__)
urllib3.disable_warnings()

UPLOAD_FOLDER = "static/uploads"
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# ======================
# LOAD MODELS
# ======================
image_model   = tf.keras.models.load_model("models/deepfake_detector.keras")
news_model    = joblib.load("model.pkl")
vectorizer    = joblib.load("vectorizer.pkl")
semantic_model = SentenceTransformer('all-MiniLM-L6-v2')

# ======================
# API KEYS
# ======================
NEWS_KEY  = os.getenv("NEWS_KEY")
GNEWS_KEY = os.getenv("GNEWS_KEY")
MEDIA_KEY = os.getenv("MEDIA_KEY")
FACT_KEY  = os.getenv("FACT_KEY")

TRUSTED_SOURCES = [
    "bbc", "reuters", "al jazeera", "cnn", "guardian",
    "associated press", "new york times", "washington post",
    "bloomberg", "financial times", "dw", "ndtv", "the hindu",
    "hindustan times", "indian express", "times of india",
    "livemint", "economic times", "apnews", "npr", "abc news",
    "nbc news", "sky news", "france 24", "the wire", "scroll"
]

# ======================
# HELPER FUNCTIONS
# ======================

def similarity(a, b):
    e1 = semantic_model.encode([a])
    e2 = semantic_model.encode([b])
    return float(cosine_similarity(e1, e2)[0][0])

def google_fact(q):
    try:
        url    = "https://factchecktools.googleapis.com/v1alpha1/claims:search"
        params = {"query": q, "key": FACT_KEY}
        r = requests.get(url, params=params, verify=False, timeout=6)
        return r.json().get("claims", []) if r.status_code == 200 else []
    except:
        return []

def google_news(q):
    try:
        url    = "https://news.google.com/rss/search"
        params = {"q": q, "hl": "en-IN", "gl": "IN", "ceid": "IN:en"}
        r = requests.get(url, params=params, timeout=6)
        return r.text.lower() if r.status_code == 200 else ""
    except:
        return ""

def newsdata(q):
    try:
        url    = "https://newsdata.io/api/1/news"
        params = {"apikey": NEWS_KEY, "q": q, "language": "en"}
        r = requests.get(url, params=params, timeout=6)
        return r.json().get("results", []) if r.status_code == 200 else []
    except:
        return []

def gnews(q):
    try:
        url    = "https://gnews.io/api/v4/search"
        params = {"q": q, "token": GNEWS_KEY, "lang": "en"}
        r = requests.get(url, params=params, timeout=6)
        return r.json().get("articles", []) if r.status_code == 200 else []
    except:
        return []

def mediastack(q):
    try:
        url    = "http://api.mediastack.com/v1/news"
        params = {"access_key": MEDIA_KEY, "keywords": q, "languages": "en"}
        r = requests.get(url, params=params, timeout=6)
        return r.json().get("data", []) if r.status_code == 200 else []
    except:
        return []

def trusted_check(text):
    t = text.lower()
    return any(src in t for src in TRUSTED_SOURCES)

def google_match_score(query, text):
    lines  = text.split("\n")
    scores = [similarity(query, line) for line in lines[:30] if len(line) > 30]
    return max(scores) if scores else 0.0

def agreement_score(query, articles):
    scores = []
    for a in articles[:10]:
        title = a.get("title") or a.get("headline", "")
        if title:
            scores.append(similarity(query, title))
    return sum(scores) / len(scores) if scores else 0.0

def ml_predict(text):
    vec  = vectorizer.transform([text])
    pred = news_model.predict(vec)[0]
    prob = news_model.predict_proba(vec)[0][pred]
    return int(pred), float(prob)

def predict_image(img_path):
    img       = image.load_img(img_path, target_size=(224, 224))
    arr       = image.img_to_array(img)
    arr       = np.expand_dims(arr, axis=0)
    arr       = preprocess_input(arr)
    raw       = image_model.predict(arr)[0][0]
    if raw > 0.5:
        return "REAL", float(raw)
    else:
        return "FAKE", float(1 - raw)

def is_fact_check_recent(claim, max_age_days=90):
    """Returns True only if the fact-check was published within max_age_days."""
    try:
        review     = claim["claimReview"][0]
        date_str   = review.get("reviewDate") or claim.get("claimDate", "")
        if not date_str:
            return False
        parsed   = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        age_days = (datetime.now(timezone.utc) - parsed).days
        return age_days <= max_age_days
    except:
        return False

# ======================
# CORE: WEIGHTED VERDICT ENGINE
# ======================
def compute_verdict(query, recent_facts, article_count, agree, google_trust, gmatch, pred, prob):
    """
    Multi-signal weighted scoring system.

    Signals and their max contribution:
      - Article volume:      0–30 pts
      - Semantic agreement:  0–25 pts
      - Trusted source:        20 pts
      - Google match score:  0–15 pts
      - ML model:          -10–+10 pts  (tiebreaker only)

    Verdict thresholds:
      score >= 45  → REAL NEWS
      score >= 25  → LIKELY REAL
      score >= 12  → UNCERTAIN
      score <  12, no evidence, ML=real → UNVERIFIED
      else         → LIKELY FAKE / FAKE NEWS
    """

    # ── TIER 1: Recent authoritative fact-check overrides everything ──
    if recent_facts:
        rating   = recent_facts[0]["claimReview"][0]["textualRating"].lower()
        is_false = any(w in rating for w in ["false", "fake", "incorrect", "mislead", "debunk", "wrong", "unverified", "not true"])
        verdict  = "FAKE NEWS" if is_false else "REAL NEWS"
        conf     = 92 if is_false else 90
        return verdict, conf

    # ── TIER 2: Weighted signal accumulation ──
    score   = 0.0
    signals = {}

    # Article volume (0–30 pts)
    if article_count >= 5:
        score += 30;  signals["articles"] = 30
    elif article_count >= 2:
        score += 18;  signals["articles"] = 18
    elif article_count == 1:
        score += 8;   signals["articles"] = 8
    # 0 articles → 0 pts (not automatically fake)

    # Semantic agreement (0–25 pts) — threshold lowered from 0.45 to 0.20
    if agree > 0.35:
        pts    = min(25, int(agree * 50))
        score += pts; signals["agreement"] = pts
    elif agree > 0.20:
        pts    = int(agree * 30)
        score += pts; signals["agreement"] = pts

    # Trusted source hit (20 pts flat)
    if google_trust:
        score += 20;  signals["trusted_source"] = 20

    # Google News match similarity (0–15 pts)
    if gmatch > 0.50:
        pts    = min(15, int(gmatch * 20))
        score += pts; signals["google_match"] = pts
    elif gmatch > 0.30:
        pts    = int(gmatch * 10)
        score += pts; signals["google_match"] = pts

    # ML model as tiebreaker (-10 to +10 pts)
    ml_pts = min(10, int(prob * 10))
    if pred == 1:   # ML says REAL
        score += ml_pts;  signals["ml"] = +ml_pts
    else:            # ML says FAKE
        score -= ml_pts;  signals["ml"] = -ml_pts

    # ── TIER 3: Map score to verdict ──
    if score >= 45:
        verdict = "REAL NEWS"
        conf    = min(94, 60 + int(score * 0.5))

    elif score >= 25:
        verdict = "LIKELY REAL"
        conf    = min(75, 45 + int(score * 0.6))

    elif score >= 12:
        verdict = "UNCERTAIN"
        conf    = 40 + int(score * 0.5)

    elif article_count == 0 and not google_trust:
        # Absolutely no external evidence — use ML as primary
        if pred == 0 and prob > 0.65:
            verdict = "LIKELY FAKE"
            conf    = min(78, int(prob * 80))
        else:
            verdict = "UNVERIFIED — No Evidence Found"
            conf    = 30 + int(prob * 10)

    else:
        verdict = "LIKELY FAKE"
        conf    = min(80, 40 + max(0, int((20 - score) * 2)))

    return verdict, conf


# ======================
# ROUTES
# ======================

@app.route("/", methods=["GET"])
def index():
    return render_template("index.html")


@app.route("/upload", methods=["POST"])
def upload_image():
    file = request.files.get("file")
    if not file:
        return jsonify({"error": "No file uploaded"}), 400

    filepath = os.path.join(app.config["UPLOAD_FOLDER"], file.filename)
    file.save(filepath)

    label, confidence = predict_image(filepath)
    return jsonify({
        "label":      label,
        "confidence": round(confidence * 100, 2),
        "filename":   file.filename
    })


@app.route("/analyze-news", methods=["POST"])
def analyze_news():
    data  = request.get_json()
    query = (data or {}).get("query", "").strip()

    if not query:
        return jsonify({"error": "Empty headline"}), 400

    # ── Gather evidence in parallel (all APIs called regardless) ──
    facts        = google_fact(query)
    google_text  = google_news(query)
    google_trust = trusted_check(google_text)
    gmatch       = google_match_score(query, google_text)

    nd       = newsdata(query)
    gn       = gnews(query)
    ms       = mediastack(query)
    articles = nd + gn + ms

    article_count = len(articles)
    agree         = agreement_score(query, articles)
    pred, prob    = ml_predict(query)

    # Filter fact-checks to recent ones only (prevents stale debunks overriding fresh news)
    recent_facts = [f for f in facts if is_fact_check_recent(f, max_age_days=90)]

    # ── Run weighted verdict engine ──
    verdict, conf = compute_verdict(
        query, recent_facts, article_count,
        agree, google_trust, gmatch,
        pred, prob
    )

    return jsonify({
        "label":      verdict,
        "confidence": conf,
        "query":      query,
        "evidence": {
            "articles_found": article_count,
            "google_trust":   google_trust,
            "google_match":   round(gmatch, 3),
            "agreement":      round(agree, 3),
            "fact_checks":    len(recent_facts),
            "ml_label":       "REAL" if pred == 1 else "FAKE",
            "ml_prob":        round(prob, 3),
        }
    })


@app.route("/debug", methods=["GET"])
def debug():
    """Health check — confirms all APIs are reachable and keys are loaded."""
    query = request.args.get("q", "test query")
    nd    = newsdata(query)
    gn    = gnews(query)
    ms    = mediastack(query)
    gt    = google_news(query)
    facts = google_fact(query)
    return jsonify({
        "newsdata_count":    len(nd),
        "gnews_count":       len(gn),
        "mediastack_count":  len(ms),
        "google_news_chars": len(gt),
        "facts_count":       len(facts),
        "keys_loaded": {
            "NEWS_KEY":  bool(NEWS_KEY),
            "GNEWS_KEY": bool(GNEWS_KEY),
            "MEDIA_KEY": bool(MEDIA_KEY),
            "FACT_KEY":  bool(FACT_KEY),
        }
    })


if __name__ == "__main__":
    app.run(debug=True, port=5000)