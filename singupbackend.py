from flask import Flask, render_template, request, redirect, url_for, session
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from flask_dance.contrib.google import make_google_blueprint, google
import os

app = Flask(__name__)
app.secret_key = "supersecretkey"
from datetime import timedelta
app.permanent_session_lifetime = timedelta(days=7)

# Allow OAuth over HTTP (ONLY for local testing)
os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"

# Database configuration
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///database.db"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db = SQLAlchemy(app)

# ------------------------
# USER MODEL
# ------------------------
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(150), unique=True, nullable=False)
    password = db.Column(db.String(200), nullable=True)  # nullable for Google users


with app.app_context():
    db.create_all()


# ------------------------
# GOOGLE OAUTH SETUP
# ------------------------
google_bp = make_google_blueprint(
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET")
    scope=[
        "openid",
        "https://www.googleapis.com/auth/userinfo.email",
        "https://www.googleapis.com/auth/userinfo.profile",
    ],
    redirect_to="google_login"
)

app.register_blueprint(google_bp, url_prefix="/login")


# ------------------------
# ROUTES
# ------------------------

@app.route("/")
def index():
    if "user_id" in session:
        return redirect(url_for("home"))
    return render_template("signup.html")


# NORMAL LOGIN
@app.route("/login", methods=["POST"])
def login():
    email = request.form.get("email")
    password = request.form.get("password")

    user = User.query.filter_by(email=email).first()

    if user and user.password and check_password_hash(user.password, password):
        session["user_id"] = user.id
        return redirect(url_for("home"))

    return "Invalid credentials"


# REGISTER
@app.route("/register", methods=["POST"])
def register():
    email = request.form.get("email")
    password = request.form.get("password")

    hashed_password = generate_password_hash(password)

    new_user = User(email=email, password=hashed_password)
    db.session.add(new_user)
    db.session.commit()

    return redirect(url_for("index"))


# GOOGLE LOGIN HANDLER
@app.route("/google_login")
def google_login():
    if not google.authorized:
        return redirect(url_for("google.login"))

    resp = google.get("/oauth2/v2/userinfo")
    if not resp.ok:
        return "Failed to fetch user info"

    user_info = resp.json()

    email = user_info.get("email")
    if not email:
        return "Google account did not return email."

    user = User.query.filter_by(email=email).first()

    if not user:
        user = User(email=email, password=None)
        db.session.add(user)
        db.session.commit()

    session.permanent = True
    session["user_id"] = user.id
    return redirect(url_for("home"))
@app.route("/home")
def home():
    if "user_id" not in session:
        return redirect(url_for("index"))

    user = User.query.get(session["user_id"])
    return render_template("home.html", user=user)


@app.route("/logout")
def logout():
    session.pop("user_id", None)
    return redirect(url_for("index"))


if __name__ == "__main__":
    app.run(debug=True)
