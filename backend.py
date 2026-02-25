from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
import requests
import uvicorn

app = FastAPI()

# 1. Mount the "static" folder so it can serve index.html
app.mount("/static", StaticFiles(directory="static"), name="static")

# 2. Redirect the main URL to your HTML file
@app.get("/")
async def read_index():
    return FileResponse('static/index.html')

# 3. Your Search Logic (Integrated from your Streamlit code)
@app.get("/analyze")
def analyze(query: str):
    # (Insert your search_newsdata, search_gnews, etc. functions here)
    # For now, a simple placeholder logic:
    confidence = 75 
    verdict = "✅ Likely Real"
    
    return {
        "verdict": verdict,
        "confidence": confidence,
        "query": query
    }

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)