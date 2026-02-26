import streamlit as st
import requests
import joblib
import urllib3

from sentence_transformers import SentenceTransformer
from sklearn.metrics.pairwise import cosine_similarity

urllib3.disable_warnings()


# ======================
# LOAD MODELS
# ======================

model = joblib.load("model.pkl")
vectorizer = joblib.load("vectorizer.pkl")

semantic_model = SentenceTransformer('all-MiniLM-L6-v2')


# ======================
# API KEYS
# ======================

NEWS_KEY = st.secrets["newsdata_key"]
GNEWS_KEY = st.secrets["gnews_key"]
MEDIA_KEY = st.secrets["mediastack_key"]
FACT_KEY = st.secrets["google_fact_key"]


# ======================
# TRUSTED SOURCES
# ======================

trusted_sources = [

"bbc","reuters","al jazeera","cnn","guardian",
"associated press","new york times","washington post",
"bloomberg","financial times","dw",

"ndtv","the hindu","hindustan times",
"indian express","times of india",
"livemint","economic times"

]


# ======================
# UI
# ======================

st.title("🧠 Ultimate Fake News Detector")

st.write(
"Fact Check + Trusted Media + Real-Time News + Machine Learning"
)

query = st.text_input("Enter headline or claim")


# ======================
# SIMILARITY
# ======================

def similarity(a,b):

    e1 = semantic_model.encode([a])
    e2 = semantic_model.encode([b])

    return cosine_similarity(e1,e2)[0][0]


# ======================
# GOOGLE FACT CHECK
# ======================

def google_fact(q):

    try:

        url="https://factchecktools.googleapis.com/v1alpha1/claims:search"

        params={
            "query":q,
            "key":FACT_KEY
        }

        r=requests.get(url,params=params,verify=False)

        if r.status_code==200:

            return r.json().get("claims",[])

        return []

    except:

        return []


# ======================
# GOOGLE NEWS
# ======================

def google_news(q):

    try:

        url="https://news.google.com/rss/search"

        params={
            "q":q,
            "hl":"en-IN",
            "gl":"IN",
            "ceid":"IN:en"
        }

        r=requests.get(url,params=params)

        if r.status_code==200:

            return r.text.lower()

        return ""

    except:

        return ""


# ======================
# NEWS APIS
# ======================

def newsdata(q):

    try:

        url="https://newsdata.io/api/1/news"

        params={
            "apikey":NEWS_KEY,
            "q":q,
            "language":"en"
        }

        r=requests.get(url,params=params)

        if r.status_code==200:

            return r.json().get("results",[])

        return []

    except:
        return []


def gnews(q):

    try:

        url="https://gnews.io/api/v4/search"

        params={
            "q":q,
            "token":GNEWS_KEY,
            "lang":"en"
        }

        r=requests.get(url,params=params)

        if r.status_code==200:

            return r.json().get("articles",[])

        return []

    except:
        return []


def mediastack(q):

    try:

        url="http://api.mediastack.com/v1/news"

        params={
            "access_key":MEDIA_KEY,
            "keywords":q,
            "languages":"en"
        }

        r=requests.get(url,params=params)

        if r.status_code==200:

            return r.json().get("data",[])

        return []

    except:
        return []


# ======================
# TRUSTED CHECK
# ======================

def trusted_check(text):

    for t in trusted_sources:

        if t in text:
            return True

    return False


# ======================
# GOOGLE MATCH SCORE
# ======================

def google_match_score(query,text):

    lines=text.split("\n")

    scores=[]

    for line in lines[:30]:

        if len(line)>30:

            s=similarity(query,line)

            scores.append(s)

    if scores:
        return max(scores)

    return 0


# ======================
# AGREEMENT SCORE
# ======================

def agreement_score(query,articles):

    scores=[]

    for a in articles[:10]:

        if "title" in a:

            s=similarity(query,a["title"])

            scores.append(s)

    if scores:
        return sum(scores)/len(scores)

    return 0


# ======================
# ML MODEL
# ======================

def ml_predict(text):

    vec=vectorizer.transform([text])

    pred=model.predict(vec)[0]

    prob=model.predict_proba(vec)[0][pred]

    return pred,prob


# ======================
# BUTTON
# ======================

if st.button("Check News"):

    if query=="":

        st.warning("Enter headline")

    else:

        with st.spinner("Analyzing News..."):

            facts = google_fact(query)

            google_text = google_news(query)

            google_trust = trusted_check(google_text)

            gmatch = google_match_score(query,google_text)

            nd = newsdata(query)
            gn = gnews(query)
            ms = mediastack(query)

            articles = nd+gn+ms

            article_count=len(articles)

            agree = agreement_score(query,articles)

            pred,prob = ml_predict(query)


        # ======================
        # EVIDENCE
        # ======================

        st.subheader("Evidence")

        st.write("Articles Found:",article_count)
        st.write("Trusted Google News:",google_trust)
        st.write("Google Match Score:",round(gmatch,2))
        st.write("Agreement Score:",round(agree,2))


        if articles:

            st.markdown("### News Articles")

            for a in articles[:6]:

                try:

                    if "title" in a and "link" in a:

                        st.markdown(f"- [{a['title']}]({a['link']})")

                    elif "title" in a and "url" in a:

                        st.markdown(f"- [{a['title']}]({a['url']})")

                except:
                    pass


        # ======================
        # FINAL VERDICT
        # ======================

        st.subheader("Final Verdict")


        # FACT CHECK PRIORITY

        if facts:

            rating=facts[0]['claimReview'][0]['textualRating']

            st.write("Fact Check:",rating)

            if "false" in rating.lower():

                st.error("❌ FAKE NEWS")

            else:

                st.success("✅ REAL NEWS")


        # MULTIPLE ARTICLES

        elif article_count>=2 and agree>0.45:

            st.success("✅ REAL NEWS")


        # SINGLE TRUSTED ARTICLE

        elif article_count==1 and google_trust and gmatch>0.5:

            st.success("✅ REAL NEWS (Trusted Article)")


        # NO EVIDENCE

        elif article_count==0:

            st.error("❌ LIKELY FAKE (No Evidence Found)")


        # ML BACKUP

        elif pred==0:

            st.error("❌ LIKELY FAKE")


        else:

            st.warning("⚠️ UNCERTAIN")