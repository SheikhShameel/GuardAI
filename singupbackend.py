"""
GuardAI — Improved Flask Backend (MERGED WITH AUTH + DB)
"""
import os, re, json, math, requests, random
from datetime import timedelta
from difflib import SequenceMatcher
from urllib.parse import quote_plus

from flask import Flask, request, jsonify, render_template, redirect, url_for, session
from flask_cors import CORS
from flask_sqlalchemy import SQLAlchemy
from flask_dance.contrib.google import make_google_blueprint, google
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

# ═══════════════════════════════════════════════════════════════
# APP SETUP
# ═══════════════════════════════════════════════════════════════
app = Flask(__name__)
CORS(app)

app.secret_key = "supersecretkey"
app.permanent_session_lifetime = timedelta(days=7)

# Allow OAuth over HTTP (ONLY for local testing)
os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"

# ------------------------
# CONFIG
# ------------------------
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///database.db"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["UPLOAD_FOLDER"] = "static/uploads"

db = SQLAlchemy(app)

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
    redirect_to="google_login"
)
app.register_blueprint(google_bp, url_prefix="/login")

# ═══════════════════════════════════════════════════════════════
# FACT CHECK ENGINE CONFIG
# ═══════════════════════════════════════════════════════════════
GOOGLE_API_KEY       = os.getenv("GOOGLE_API_KEY", "AQ.Ab8RN6IPLGaOg5iPAPmoEvzmTRcOrmDA5iS4rMP91Vnd_Hp6CA")
GOOGLE_CSE_ID        = os.getenv("GOOGLE_CSE_ID", "16d5f85dff0c440a1")
FACT_CHECK_API_KEY   = os.getenv("FACT_CHECK_API_KEY", GOOGLE_API_KEY)
NEWSDATA_API_KEY     = os.getenv("NEWSDATA_API_KEY", "pub_ac173e168fcf4f398a2bfdba4cb42fd5")
GNEWS_API_KEY        = os.getenv("GNEWS_API_KEY", "a5f8bf0087f6b5f211e06dd422d32eb0")
SERPAPI_KEY          = os.getenv("SERPAPI_KEY", "")

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
# SEARCH FUNCTIONS (UNCHANGED)
# ═══════════════════════════════════════════════════════════════
def google_search(query, num=10):
    results = []
    if GOOGLE_API_KEY:
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
# SCORING ENGINE (UNCHANGED)
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

    trusted_found=set()
    fake_found=set()
    best_sim=0

    for item in google_results:
        dom=item["domain"]
        if dom in TRUSTED_DOMAINS: trusted_found.add(dom)
        if dom in FAKE_DOMAINS: fake_found.add(dom)
        best_sim=max(best_sim, similarity(claim,item["title"]))

    score += min(len(trusted_found)*25,50)
    if fake_found: score -= 30
    if best_sim>=0.65: score+=20

    if not google_results and not news_results and not fact_check.get("found"):
        score-=20

    score=max(0,min(100,score))

    label="REAL" if score>=70 else "UNCERTAIN" if score>=45 else "FAKE"
    verdict="Claim Verified" if label=="REAL" else "Claim Disputed" if label=="FAKE" else "Insufficient Evidence"

    return {"label":label,"verdict":verdict,"confidence":round(score),"details":details}

# ═══════════════════════════════════════════════════════════════
# AUTH ROUTES (UNCHANGED)
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

@app.route("/login", methods=["POST"])
def login():
    email=request.form.get("email")
    password=request.form.get("password")
    user=User.query.filter_by(email=email).first()
    if user and user.password and check_password_hash(user.password,password):
        session["user_id"]=user.id
        return redirect(url_for("index_page"))
    return "Invalid credentials"

@app.route("/register", methods=["POST"])
def register():
    email=request.form.get("email")
    password=request.form.get("password")
    hashed_password=generate_password_hash(password)
    new_user=User(email=email,password=hashed_password)
    db.session.add(new_user)
    db.session.commit()
    session["user_id"]=new_user.id
    return redirect(url_for("index_page"))

@app.route("/google_login")
def google_login():
    if not google.authorized:
        return redirect(url_for("google.login"))
    resp=google.get("/oauth2/v2/userinfo")
    if not resp.ok:
        return "Failed to fetch user info"
    email=resp.json().get("email")
    user=User.query.filter_by(email=email).first()
    if not user:
        user=User(email=email,password=None)
        db.session.add(user)
        db.session.commit()
    session["user_id"]=user.id
    return redirect(url_for("index_page"))

@app.route("/logout")
def logout():
    session.pop("user_id",None)
    return redirect(url_for("index"))

# ═══════════════════════════════════════════════════════════════
# AI ROUTES
# ═══════════════════════════════════════════════════════════════
@app.route("/analyze-news", methods=["POST"])
def analyze_news():
    data=request.get_json(force=True)
    query=data.get("query","").strip()
    if not query:
        return jsonify({"error":"No query provided"}),400
    claim=normalize_claim(query)
    google_results=google_search(claim,10)
    fact_check=fact_check_lookup(claim)
    news_results=newsdata_search(claim)+gnews_search(claim)
    result=score_results(claim,google_results,news_results,fact_check)
    result["query"]=query
    return jsonify(result)

@app.route("/upload", methods=["POST"])
def upload():
    confidence=random.randint(60,99)
    label="REAL" if confidence>70 else "FAKE"
    return jsonify({"label":label,"confidence":confidence})

# ═══════════════════════════════════════════════════════════════
# SAVE SCANS (UNCHANGED)
# ═══════════════════════════════════════════════════════════════
@app.route("/save_text_scan", methods=["POST"])
def save_text_scan():
    if "user_id" not in session:
        return {"error":"Not logged in"},401
    scan=Scan(
        user_id=session["user_id"],
        scan_type="text",
        input_data=request.form.get("text"),
        result=request.form.get("result"),
        confidence=float(request.form.get("confidence"))
    )
    db.session.add(scan)
    db.session.commit()
    return {"message":"Saved"}

@app.route("/save_image_scan", methods=["POST"])
def save_image_scan():
    if "user_id" not in session:
        return {"error":"Not logged in"},401
    file=request.files["image"]
    filename=secure_filename(file.filename)
    os.makedirs(app.config["UPLOAD_FOLDER"],exist_ok=True)
    path=os.path.join(app.config["UPLOAD_FOLDER"],filename)
    file.save(path)
    scan=Scan(
        user_id=session["user_id"],
        scan_type="image",
        input_data=filename,
        result=request.form.get("result"),
        confidence=float(request.form.get("confidence"))
    )
    db.session.add(scan)
    db.session.commit()
    return {"message":"Saved"}

@app.route("/my_scans")
def my_scans():
    if "user_id" not in session:
        return redirect(url_for("index"))
    scans=Scan.query.filter_by(user_id=session["user_id"]).order_by(Scan.created_at.desc()).all()
    return render_template("my_scans.html", scans=scans)

# ═══════════════════════════════════════════════════════════════
# RUN
# ═══════════════════════════════════════════════════════════════
if __name__ == "__main__":
    app.run(debug=True, port=5000)  