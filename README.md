# BSE FireTag — disposable demo

SS 578:2019 fire-extinguisher maintenance demo. Single-file Flask app,
SQLite seeded on startup. Not production (no auth; data resets on cold start).

## Deploy a public URL on Render (free, no card)

1. Put these 4 files in a GitHub repo (app.py, requirements.txt, Procfile, render.yaml).
2. Go to https://render.com → New → Web Service → connect the repo.
3. Render auto-detects render.yaml. Click Create. Wait ~2 min.
4. You get a public https://bse-firetag-demo.onrender.com URL — open on your phone.

Free tier sleeps when idle; first hit after sleep takes ~30s to wake, then resets demo data.

## Or run locally
    pip install -r requirements.txt
    python app.py
    # open http://127.0.0.1:8080
