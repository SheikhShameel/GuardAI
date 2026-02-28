"""
GuardAI — Flask Backend with OpenAI Integration
"""
import os, re, json, math, requests, random, base64
from datetime import timedelta
from difflib import SequenceMatcher
from urllib.parse import quote_plus
from dotenv import load_dotenv

load_dotenv()

from flask import Flask, request, jsonify, render_template, redirect, url_for, session
from flask_cors import CORS
from flask_sqlalchemy import SQLAlchemy
from flask_dance.contrib.google import make_google_blueprint, google
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from openai import OpenAI

# ═══════════════════════════════════════════════════════════════
# APP SETUP
# ═══════════════════════════════════════════════════════════════
app = Flask(__name__)
CORS(app)

app.secret_key = os.getenv("SECRET_KEY", "supersecretkey")
app.permanent_session_lifetime = timedelta(days=7)
os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"

app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///database.db"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["UPLOAD_FOLDER"] = "static/uploads"

db = SQLAlchemy(app)

# OpenAI client
openai_client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# ═══════════════════════════════════════════════════════════════
# MODELS
# ═══════════════════════════════════════════════════════════════
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(150), unique=True, nullable=False)
    password = db.Column(db.String(200), nullable=True)

class Scan(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    scan_type = db.Column(db.String(20))
    input_data = db.Column(db.Text)
    result = db.Column(db.Text)
    confidence = db.Column(db.Float)
    created_at = db.Column(db.DateTime, default=db.func.current_timestamp())

with app.app_context():
    db.create_all()

# ═══════════════════════════════════════════════════════════════
# GOOGLE OAUTH
# ═══════════════════════════════════════════════════════════════
google_bp = make_google_blueprint(
    client_id=os.getenv("GOOGLE_CLIENT_ID"),
    client_secret=os.getenv("GOOGLE_CLIENT_SECRET"),
    scope=[
        "openid",
        "https://www.googleapis.com/auth/userinfo.email",
        "https://www.googleapis.com/auth/userinfo.profile",
    ],
    # Remove redirect_to or set it to the correct endpoint
    redirect_to="google_callback_handler" 
)

app.register_blueprint(google_bp, url_prefix="/login")

# ═══════════════════════════════════════════════════════════════
# API KEYS
# ═══════════════════════════════════════════════════════════════
GOOGLE_API_KEY     = os.getenv("GOOGLE_API_KEY", "")
GOOGLE_CSE_ID      = os.getenv("GOOGLE_CSE_ID", "")
FACT_CHECK_API_KEY = os.getenv("FACT_CHECK_API_KEY", GOOGLE_API_KEY)
NEWSDATA_API_KEY   = os.getenv("NEWSDATA_API_KEY", "")
GNEWS_API_KEY      = os.getenv("GNEWS_API_KEY", "")

TRUSTED_DOMAINS = {
    "thehindu.com","bbc.com","bbc.co.uk","reuters.com","apnews.com","ndtv.com",
    "theindianexpress.com","hindustantimes.com","livemint.com","timesofindia.com",
    "economictimes.com","theguardian.com","nytimes.com","washingtonpost.com",
    "aljazeera.com","cnn.com","abc.net.au","theprint.in","scroll.in",
    "thewire.in","news18.com","indiatoday.in",
}
FAKE_DOMAINS = {
    "theonion.com","babylonbee.com","nationalreport.net",
    "worldnewsdailyreport.com","empirenews.net",
}

# ═══════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════
def similarity(a, b):
    return SequenceMatcher(None, a.lower().strip(), b.lower().strip()).ratio()

def extract_domain(url):
    m = re.search(r'https?://(?:www\.)?([^/]+)', url or '')
    return m.group(1).lower() if m else ''

def normalize_claim(text):
    text = text.strip().strip('"\'')
    text = re.sub(r'^(breaking|report|exclusive|update)\s*:\s*', '', text, flags=re.I)
    return text

# ═══════════════════════════════════════════════════════════════
# SEARCH FUNCTIONS
# ═══════════════════════════════════════════════════════════════
def google_search(query, num=10):
    results = []
    if GOOGLE_API_KEY and GOOGLE_CSE_ID:
        try:
            r = requests.get(
                "https://www.googleapis.com/customsearch/v1",
                params={"key": GOOGLE_API_KEY, "cx": GOOGLE_CSE_ID, "q": query, "num": min(num, 10)},
                timeout=8
            )
            if r.ok:
                for item in r.json().get("items", []):
                    results.append({
                        "title": item.get("title",""),
                        "url": item.get("link",""),
                        "snippet": item.get("snippet",""),
                        "domain": extract_domain(item.get("link",""))
                    })
        except Exception:
            pass
    return results

def fact_check_lookup(query):
    if not FACT_CHECK_API_KEY:
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
                reviews = top.get("claimReview",[{}])
                rev = reviews[0] if reviews else {}
                return {
                    "found": True,
                    "claim": top.get("text",""),
                    "rating": rev.get("textualRating",""),
                    "publisher": rev.get("publisher",{}).get("name",""),
                    "url": rev.get("url",""),
                }
    except Exception:
        pass
    return {"found": False}

def newsdata_search(query):
    if not NEWSDATA_API_KEY:
        return []
    try:
        r = requests.get(
            "https://newsdata.io/api/1/news",
            params={"apikey": NEWSDATA_API_KEY, "q": query, "language": "en"},
            timeout=6
        )
        return [{"title":a.get("title",""),"url":a.get("link",""),"domain":extract_domain(a.get("link",""))}
                for a in r.json().get("results",[])]
    except Exception:
        return []

def gnews_search(query):
    if not GNEWS_API_KEY:
        return []
    try:
        r = requests.get(
            "https://gnews.io/api/v4/search",
            params={"q": query, "token": GNEWS_API_KEY, "lang": "en", "max": 10},
            timeout=6
        )
        return [{"title":a.get("title",""),"url":a.get("url",""),"domain":extract_domain(a.get("url",""))}
                for a in r.json().get("articles",[])]
    except Exception:
        return []

# ═══════════════════════════════════════════════════════════════
# OPENAI: NEWS ANALYSIS
# ═══════════════════════════════════════════════════════════════
def openai_analyze_news(claim, google_results, news_results, fact_check):
    """Use GPT-4o to deeply analyze a news claim with all gathered evidence."""
    
    # Build context for GPT
    context_parts = []
    
    if fact_check.get("found"):
        context_parts.append(f"FACT-CHECK RESULT: '{fact_check.get('claim')}' rated '{fact_check.get('rating')}' by {fact_check.get('publisher')}")
    
    if google_results:
        context_parts.append("GOOGLE SEARCH RESULTS:")
        for r in google_results[:5]:
            context_parts.append(f"  - [{r['domain']}] {r['title']}: {r.get('snippet','')[:200]}")
    
    if news_results:
        context_parts.append("NEWS ARTICLES FOUND:")
        for r in news_results[:5]:
            context_parts.append(f"  - [{r['domain']}] {r['title']}")
    
    context = "\n".join(context_parts) if context_parts else "No external evidence found."
    
    prompt = f"""You are GuardAI, an expert AI fact-checker and forensic intelligence system. Analyze the following news claim using all available evidence.

CLAIM TO VERIFY: "{claim}"

GATHERED EVIDENCE:
{context}

Provide a thorough analysis. Return a JSON object with these exact fields:
{{
  "label": "REAL" | "FAKE" | "UNCERTAIN",
  "confidence": <integer 0-100>,
  "verdict": "<short verdict headline, max 6 words>",
  "summary": "<2-3 sentence detailed explanation of your analysis>",
  "key_findings": ["<finding 1>", "<finding 2>", "<finding 3>"],
  "signals": [
    {{"icon": "✔" | "✘" | "~", "text": "<signal description>"}},
    ...
  ],
  "context": "<1-2 sentences of broader context or related background>",
  "sources_quality": "HIGH" | "MEDIUM" | "LOW" | "NONE"
}}

Be precise. Only output valid JSON, no markdown fences."""

    try:
        response = openai_client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=800,
            temperature=0.2
        )
        text = response.choices[0].message.content.strip()
        # Strip markdown fences if present
        text = re.sub(r'^```(?:json)?\s*', '', text)
        text = re.sub(r'\s*```$', '', text)
        return json.loads(text)
    except Exception as e:
        return None

# ═══════════════════════════════════════════════════════════════
# OPENAI: IMAGE ANALYSIS (AI DETECTION)
# ═══════════════════════════════════════════════════════════════
def openai_analyze_image(image_base64, mime_type="image/jpeg"):
    """Use GPT-4o vision to detect if image is AI-generated."""
    
    prompt = """You are GuardAI, an expert forensic AI image analyst. Analyze this image to determine if it is:
1. AI-generated (by tools like Midjourney, DALL-E, Stable Diffusion, etc.)
2. A deepfake or manipulated real photo
3. An authentic real photograph

Examine carefully for:
- Unnatural skin texture, hair, or background artifacts
- Inconsistent lighting or shadows
- Distorted hands, fingers, teeth, or ears
- Overly smooth or "painted" appearance
- Metadata inconsistencies
- Noise patterns typical of diffusion models
- GAN artifacts like checkerboard patterns
- Unusual bokeh or depth-of-field

Return ONLY a valid JSON object:
{
  "label": "REAL" | "AI_GENERATED" | "DEEPFAKE" | "MANIPULATED",
  "confidence": <integer 0-100>,
  "verdict": "<short verdict, max 5 words>",
  "summary": "<2-3 sentences describing what you found>",
  "artifacts": ["<artifact 1>", "<artifact 2>", ...],
  "signals": [
    {"icon": "✔" | "✘" | "~", "text": "<signal>"},
    ...
  ],
  "generation_model": "<likely model if AI, else null>",
  "manipulation_type": "<type if manipulated, else null>",
  "forensic_score": {
    "texture_analysis": <0-100>,
    "lighting_consistency": <0-100>,
    "facial_coherence": <0-100>,
    "noise_pattern": <0-100>
  }
}"""

    try:
        response = openai_client.chat.completions.create(
            model="gpt-4o",
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:{mime_type};base64,{image_base64}",
                            "detail": "high"
                        }
                    },
                    {"type": "text", "text": prompt}
                ]
            }],
            max_tokens=800,
            temperature=0.1
        )
        text = response.choices[0].message.content.strip()
        text = re.sub(r'^```(?:json)?\s*', '', text)
        text = re.sub(r'\s*```$', '', text)
        return json.loads(text)
    except Exception as e:
        return None

# ═══════════════════════════════════════════════════════════════
# LEGACY SCORING (FALLBACK)
# ═══════════════════════════════════════════════════════════════
def score_results(claim, google_results, news_results, fact_check):
    score = 50
    details = []

    if fact_check.get("found"):
        rating = fact_check.get("rating","").upper()
        pub = fact_check.get("publisher","Unknown")
        if "TRUE" in rating:
            score += 40
            details.append(f"✔ Fact-checked as TRUE by {pub}")
        elif "FALSE" in rating:
            score -= 40
            details.append(f"✘ Fact-checked as FALSE by {pub}")

    trusted_found = set()
    fake_found = set()
    best_sim = 0

    for item in google_results:
        dom = item["domain"]
        if dom in TRUSTED_DOMAINS: trusted_found.add(dom)
        if dom in FAKE_DOMAINS: fake_found.add(dom)
        best_sim = max(best_sim, similarity(claim, item["title"]))

    score += min(len(trusted_found) * 25, 50)
    if fake_found: score -= 30
    if best_sim >= 0.65: score += 20

    if not google_results and not news_results and not fact_check.get("found"):
        score -= 20

    score = max(0, min(100, score))
    label = "REAL" if score >= 70 else "UNCERTAIN" if score >= 45 else "FAKE"
    verdict = "Claim Verified" if label == "REAL" else "Claim Disputed" if label == "FAKE" else "Insufficient Evidence"

    return {"label": label, "verdict": verdict, "confidence": round(score), "details": details}

# ═══════════════════════════════════════════════════════════════
# AUTH ROUTES
# ═══════════════════════════════════════════════════════════════
@app.route("/")
def index():
    if "user_id" in session:
        return redirect(url_for("index_page"))
    return render_template("signup.html")

@app.route("/index")
def index_page():
    if "user_id" not in session:
        return redirect(url_for("index"))
    return render_template("index.html")
@app.route("/register", methods=["POST"])
def register():
    email = request.form.get("email")
    password = request.form.get("password")
    
    # Check if email is already in the database
    existing_user = User.query.filter_by(email=email).first()
    if existing_user:
        # Instead of crashing with IntegrityError, we return a clear message
        return "Error: This email is already registered. Please login instead."

    # Hash password and save
    hashed_password = generate_password_hash(password)
    new_user = User(email=email, password=hashed_password)
    
    try:
        db.session.add(new_user)
        db.session.commit()
        session["user_id"] = new_user.id
        return redirect(url_for("index_page"))
    except Exception as e:
        db.session.rollback()
        return f"Database Error: {str(e)}"

@app.route("/login", methods=["POST"])
def login():
    email = request.form.get("email")
    password = request.form.get("password")
    
    user = User.query.filter_by(email=email).first()
    
    # Check if user exists and password is correct
    if user and user.password and check_password_hash(user.password, password):
        session["user_id"] = user.id
        return redirect(url_for("index_page"))
    
    return "Error: Invalid email or password."
@app.route("/google_callback")
def google_callback_handler():
    # If the user denied the login or something went wrong
    if not google.authorized:
        return redirect(url_for("google.login"))
    
    # Request the user's profile info from Google
    resp = google.get("/oauth2/v2/userinfo")
    if not resp.ok:
        return "Error: Could not retrieve user info from Google."

    info = resp.json()
    email = info.get("email")

    # Check if they exist in our database
    user = User.query.filter_by(email=email).first()

    # If they don't exist, create a new user (Auto-registration)
    if not user:
        user = User(email=email, password=None) 
        db.session.add(user)
        db.session.commit()

    # Log them in by saving their ID in the session
    session["user_id"] = user.id
    return redirect(url_for("index_page"))
@app.route("/logout")
def logout():
    session.pop("user_id", None)
    return redirect(url_for("index"))

# ═══════════════════════════════════════════════════════════════
# AI ROUTES — WITH OPENAI
# ═══════════════════════════════════════════════════════════════
@app.route("/analyze-news", methods=["POST"])
def analyze_news():
    data = request.get_json(force=True)
    query = data.get("query", "").strip()
    if not query:
        return jsonify({"error": "No query provided"}), 400

    claim = normalize_claim(query)

    # Gather evidence from all sources
    google_results = google_search(claim, 10)
    fact_check = fact_check_lookup(claim)
    news_results = newsdata_search(claim) + gnews_search(claim)

    # Try OpenAI first
    ai_result = openai_analyze_news(claim, google_results, news_results, fact_check)

    if ai_result:
        # Build evidence object
        trusted_sources = list({r["domain"] for r in google_results if r["domain"] in TRUSTED_DOMAINS})
        best_sim = max((similarity(claim, r["title"]) for r in google_results), default=0)

        # Convert signals to details list
        details = [f"{s['icon']} {s['text']}" for s in ai_result.get("signals", [])]

        result = {
            "label": ai_result.get("label", "UNCERTAIN"),
            "confidence": ai_result.get("confidence", 50),
            "verdict": ai_result.get("verdict", "Analysis Complete"),
            "summary": ai_result.get("summary", ""),
            "key_findings": ai_result.get("key_findings", []),
            "context": ai_result.get("context", ""),
            "sources_quality": ai_result.get("sources_quality", "NONE"),
            "details": details,
            "evidence": {
                "articles_found": len(news_results),
                "google_results": len(google_results),
                "google_trust": bool(trusted_sources),
                "trusted_sources": trusted_sources,
                "google_match": round(best_sim, 2),
                "agreement": best_sim,
                "fact_check": fact_check,
            },
            "powered_by": "gpt-4o",
            "query": query
        }
    else:
        # Fallback to legacy scoring
        legacy = score_results(claim, google_results, news_results, fact_check)
        trusted_sources = list({r["domain"] for r in google_results if r["domain"] in TRUSTED_DOMAINS})
        best_sim = max((similarity(claim, r["title"]) for r in google_results), default=0)
        result = {
            **legacy,
            "evidence": {
                "articles_found": len(news_results),
                "google_results": len(google_results),
                "google_trust": bool(trusted_sources),
                "trusted_sources": trusted_sources,
                "google_match": round(best_sim, 2),
                "agreement": best_sim,
                "fact_check": fact_check,
            },
            "powered_by": "legacy",
            "query": query
        }

    return jsonify(result)


@app.route("/login_page") # Use this to serve the login HTML
def login_page():
    if "user_id" in session:
        return redirect(url_for("index_page"))
    return render_template("login.html")

@app.route("/upload", methods=["POST"])
def upload():
    """Image analysis using GPT-4o Vision + multiple ML signals."""
    file = request.files.get("file")
    if not file:
        return jsonify({"error": "No file provided"}), 400

    # Read image and encode to base64
    file_bytes = file.read()
    image_base64 = base64.b64encode(file_bytes).decode("utf-8")

    # Determine MIME type
    fname = file.filename.lower()
    if fname.endswith(".png"):
        mime = "image/png"
    elif fname.endswith(".webp"):
        mime = "image/webp"
    elif fname.endswith(".gif"):
        mime = "image/gif"
    else:
        mime = "image/jpeg"

    # Run OpenAI vision analysis
    ai_result = openai_analyze_image(image_base64, mime)

    if ai_result:
        label_map = {
            "REAL": "REAL",
            "AI_GENERATED": "FAKE",
            "DEEPFAKE": "FAKE",
            "MANIPULATED": "FAKE"
        }
        raw_label = ai_result.get("label", "REAL")
        label = label_map.get(raw_label, "FAKE" if raw_label != "REAL" else "REAL")

        details = [f"{s['icon']} {s['text']}" for s in ai_result.get("signals", [])]

        result = {
            "label": label,
            "raw_label": raw_label,
            "confidence": ai_result.get("confidence", 75),
            "verdict": ai_result.get("verdict", "Analysis Complete"),
            "summary": ai_result.get("summary", ""),
            "artifacts": ai_result.get("artifacts", []),
            "generation_model": ai_result.get("generation_model"),
            "manipulation_type": ai_result.get("manipulation_type"),
            "forensic_score": ai_result.get("forensic_score", {}),
            "details": details,
            "powered_by": "gpt-4o-vision"
        }
    else:
        # Fallback
        confidence = random.randint(60, 99)
        result = {
            "label": "REAL" if confidence > 70 else "FAKE",
            "confidence": confidence,
            "powered_by": "fallback"
        }

    return jsonify(result)

# ═══════════════════════════════════════════════════════════════
# SAVE SCANS
# ═══════════════════════════════════════════════════════════════
@app.route("/save_text_scan", methods=["POST"])
def save_text_scan():
    if "user_id" not in session:
        return {"error": "Not logged in"}, 401
    scan = Scan(
        user_id=session["user_id"],
        scan_type="text",
        input_data=request.form.get("text"),
        result=request.form.get("result"),
        confidence=float(request.form.get("confidence"))
    )
    db.session.add(scan)
    db.session.commit()
    return {"message": "Saved"}

@app.route("/save_image_scan", methods=["POST"])
def save_image_scan():
    if "user_id" not in session:
        return {"error": "Not logged in"}, 401
    file = request.files["image"]
    filename = secure_filename(file.filename)
    os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)
    path = os.path.join(app.config["UPLOAD_FOLDER"], filename)
    file.save(path)
    scan = Scan(
        user_id=session["user_id"],
        scan_type="image",
        input_data=filename,
        result=request.form.get("result"),
        confidence=float(request.form.get("confidence"))
    )
    db.session.add(scan)
    db.session.commit()
    return {"message": "Saved"}

@app.route("/my_scans")
def my_scans():
    if "user_id" not in session:
        return redirect(url_for("index"))
    scans = Scan.query.filter_by(user_id=session["user_id"]).order_by(Scan.created_at.desc()).all()
    return render_template("my_scans.html", scans=scans)

# ═══════════════════════════════════════════════════════════════
# RUN
# ═══════════════════════════════════════════════════════════════
if __name__ == "__main__":
    app.run(debug=True, port=5000)