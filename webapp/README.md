# Capsule Week

A weekly outfit planner: pairs one top + one bottom per day from your
photographed wardrobe, matched to Pune's real forecast. Add new photos
and Gemini (running locally) works out the category, color, and season
itself.

## Run it (first time)

```bash
cd webapp
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python3 server.py
```

Then open **http://localhost:5050** in your browser.

## Run it (every time after)

```bash
cd webapp
source .venv/bin/activate
python3 server.py
```

## What's in here

- `index.html` - the whole app: this week's plan, the full closet, and
  the "Add a piece" form. All 19 wardrobe photos are baked in.
- `server.py` - a tiny Flask server. Serves `index.html` and exposes
  `POST /api/classify`, which sends an uploaded photo to Gemini and
  returns `{category, type, color, season}`.
- `capsule-week-standalone.html` - the same app with the "Add a piece"
  step simplified to manual Top/Bottom + Summer/Winter picks instead of
  Gemini auto-tagging. No server, no Python - just open this one file
  directly in a browser. New pieces persist the same way (localStorage).
- `.env` - holds `GEMINI_API_KEY` (already set, reused from the capstone
  project). Get your own free key at aistudio.google.com/apikey if
  you ever need to swap it.
- New pieces you add are saved in your browser's local storage - they
  stick around next time you open the page in the same browser, but
  don't sync anywhere else.

This folder is a personal extra built on top of the graded capstone work
(the Skill + agent + MCP servers on the `feature/*` branches) - it isn't
part of the graded submission itself.

## Honesty note

Gemini's free tier caps out at 20 image calls/day - if "Add a piece"
says it couldn't classify a photo, that's very likely why. It resets
the next day.
