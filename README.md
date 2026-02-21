# DayEdge — Evening Stock Scanner
### Step-by-Step Setup Guide for Beginners

---

## What You're Building
A web dashboard that scans stocks every evening, scores them based on 4 criteria 
(Gap %, Pre-market Volume, Catalyst, Technical Level), and shows you a ranked 
watchlist so you're ready to trade the next morning.

---

## STEP 1: Get Your Alpaca API Keys

1. Go to https://alpaca.markets and create a free account
2. Once logged in, click **"Paper Trading"** in the top-left menu
3. On the right side panel, find **"Your API Keys"**
4. Click **"Generate New Key"**
5. **IMPORTANT**: Copy and save both values:
   - API Key ID (looks like: `PKXXXXXXXXXXXXXXXX`)
   - Secret Key (looks like: `XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX`)
   
   ⚠️ You cannot see the secret again after closing the window!

---

## STEP 2: Put Your API Keys Into the Project

Open the file `scanner.py` and find these two lines near the top:

```python
API_KEY = os.environ.get("ALPACA_API_KEY", "YOUR_API_KEY_HERE")
API_SECRET = os.environ.get("ALPACA_API_SECRET", "YOUR_API_SECRET_HERE")
```

For local testing only, you can replace `YOUR_API_KEY_HERE` and `YOUR_API_SECRET_HERE` 
with your actual keys. But for deployment, we'll use environment variables (more secure).

---

## STEP 3: Test It Locally (Optional)

If you have Python installed on your computer:

```bash
# Install dependencies
pip install -r requirements.txt

# Run the scanner
python scanner.py

# Start the web server
python app.py
```

Then open http://localhost:5000 in your browser.

---

## STEP 4: Deploy to Render.com (Access From Anywhere)

This makes it available from any browser, on any device, for free.

### 4a. Upload to GitHub
1. Create a free account at https://github.com
2. Create a new repository called `dayedge-scanner`
3. Upload all the project files to that repository
   - You can drag and drop files into GitHub in your browser

### 4b. Deploy on Render
1. Go to https://render.com and create a free account
2. Click **"New +"** → **"Web Service"**
3. Connect your GitHub account and select your `dayedge-scanner` repository
4. Render will auto-detect the `render.yaml` settings
5. Before clicking Deploy, go to **"Environment Variables"** and add:
   - `ALPACA_API_KEY` = your API Key ID from Step 1
   - `ALPACA_API_SECRET` = your Secret Key from Step 1
6. Click **"Create Web Service"**
7. Wait 2-3 minutes for it to build and deploy
8. Render gives you a URL like `https://dayedge-scanner.onrender.com`

**That URL is your dashboard — bookmark it!**

---

## STEP 5: Using the Dashboard

- **Run Scan Now** button: Click this any time to scan manually
- **Auto-scan**: Runs automatically every weekday at 6:00 PM ET
- **Click any row**: Expands to show the detailed scoring breakdown
- **Grades**: A = highest conviction (score 8+), B = good (6-7), C = watchlist (3-5)

---

## Understanding the Scores

| Factor | Max Points | What it means |
|--------|-----------|---------------|
| Gap % | 3 pts | 2-8% gap is ideal for day trading |
| Rel. Volume | 3 pts | 2x+ average = institutional interest |
| Technical Level | 3 pts | Near high, above MA, strong close |
| Catalyst | 1 pt | News detected today |

**Score 8+** = Grade A, highest probability setup, top priority
**Score 6-7** = Grade B, good setup worth watching
**Score 3-5** = Grade C, possible but needs more confirmation

---

## Optional: Add News Detection

For news catalyst detection, get a free API key at https://newsapi.org
Add it as `NEWS_API_KEY` in your Render environment variables.
Free tier allows 100 requests/day which is enough for the scanner.

---

## Files in This Project

```
stock-scanner/
├── app.py           # Web server (Flask)
├── scanner.py       # The actual stock scanner logic
├── requirements.txt # Python packages needed
├── render.yaml      # Render.com deployment config
├── static/
│   └── index.html   # The web dashboard UI
└── README.md        # This file
```

---

## Troubleshooting

**"No scan results yet"** — Click "Run Scan Now"

**"Could not fetch market data"** — Check your API keys are correct in environment variables

**Dashboard not loading** — Check Render logs: in your Render dashboard click "Logs"

**Scan runs but finds 0 results** — Market may be closed. Results are based on the previous trading day.

---

## Important Disclaimer

This tool is for informational and educational purposes only.
It does not constitute financial advice. Day trading involves 
substantial risk of loss. Past performance of any scanning 
methodology does not guarantee future results.
