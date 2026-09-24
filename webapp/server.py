#!/usr/bin/env python3
"""
Capsule Week - local server

Serves the Capsule Week outfit planner and exposes:
  POST /api/classify      - classify one uploaded photo via Gemini vision,
                             returns {category, type, color, season}
  GET  /api/drive/catalog - the cached wardrobe synced from Google Drive
  POST /api/drive/sync    - re-scan the Drive wardrobe folder, classify any
                             new photos via Gemini, cache the result

First time:
    pip install -r requirements.txt
Every time:
    python3 server.py
Then open http://localhost:5050 in your browser.

Needs GEMINI_API_KEY in a .env file next to this script (already set up
here, reused from the capstone project), and Drive OAuth credentials at
.credentials/drive_client_secret.json + drive_token.json (also reused).
"""
import base64
import io
import json
import os
import re

from dotenv import load_dotenv
from flask import Flask, jsonify, request, send_from_directory
from google import genai
from google.genai import errors as genai_errors
from google.genai import types
from google.auth.transport.requests import Request as GoogleAuthRequest
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload

load_dotenv()

APP_DIR = os.path.dirname(os.path.abspath(__file__))
app = Flask(__name__, static_folder=None)

GEMINI_MODEL = "gemini-3.6-flash"
_client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])

CATEGORIES = ("top", "bottom", "outerwear", "footwear", "accessory", "special")

CLASSIFY_PROMPT = """You are the wardrobe-attribute-extractor skill: given a photo of a single
wardrobe item (clothing, shoes, or an accessory), produce one structured record for a digital
wardrobe catalog.

Look at the attached photo and reply with ONLY a JSON object, no prose, no markdown fences:
{
  "category": one of "top", "bottom", "outerwear", "footwear", "accessory", "special" - whichever
    this item is ("outerwear" for jackets/coats, "footwear" for any shoe/boot/sandal,
     "accessory" for bags, belts, hats, jewelry, scarves, gloves, etc., "special" for gowns,
     sparkly/sequined/embellished pieces, or other one-piece special-occasion looks that
     shouldn't be mixed into daily top+bottom rotation),
  "type": a short lowercase description, e.g. "linen button-down shirt", "pleated midi skirt",
    "white leather sneakers", or "brown leather crossbody bag",
  "color": the dominant color in plain language, e.g. "sage green",
  "season": "summer" or "winter" - which this piece better suits (lightweight/breathable -> summer, warm/heavy -> winter)
}"""

# ---------------------------------------------------------------- Drive sync
DRIVE_FOLDER_ID = "1B0IwdizHE2YOnwmFQwh1QllEvCS9tVV3"
CREDS_DIR = os.path.join(APP_DIR, ".credentials")
CLIENT_SECRET_PATH = os.path.join(CREDS_DIR, "drive_client_secret.json")
TOKEN_PATH = os.path.join(CREDS_DIR, "drive_token.json")
CACHE_PATH = os.path.join(APP_DIR, "drive_cache.json")
DRIVE_SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]

# ------------------------------------------------------------- outfit log
# Manually logged past outfits - real daily-outfit photos for specific dates,
# kept separate from the Drive-synced wardrobe. Each photo lives at
# logged_outfits/<YYYY-MM-DD>.<ext>; pieces links to catalog item_ids where a
# piece in the photo matches something already in the closet, pieces_text
# covers anything that doesn't (freeform, since this isn't run through the
# single-item Gemini classifier).
OUTFIT_LOG_DIR = os.path.join(APP_DIR, "logged_outfits")
OUTFIT_LOG_PIECES = {
    "2026-09-02": ["satin-halter-top-light-blue-1upry1", "high-waisted-wide-leg-jeans-light-blue-1z-vg", "navy-longchamp-tote", "adidas-spezial-navy-sneakers"],
    "2026-09-03": ["high-waisted-wide-leg-jeans-light-blue-1z-vg", "navy-longchamp-tote", "onitsuka-tiger-yellow-sneakers"],
    "2026-09-04": ["pinstripe-button-down-blouse-cream", "cable-knit-wide-leg-trousers-taupe", "navy-polo-baseball-cap", "navy-longchamp-tote", "chestnut-ugg-boots"],
    "2026-09-05": ["striped-short-sleeve-polo-shirt-navy-blue-and-white-11froa", "tailored-belted-shorts-khaki", "brown-leather-belt-gold-buckle", "navy-longchamp-tote", "cream-black-mary-jane-flats"],
    "2026-09-06": ["cropped-turtleneck-sweater-burgundy-1by-nj", "black-leather-wide-leg-pants-black-1xyvzy", "black-pointed-ankle-boots"],
    "2026-09-07": ["draped-top-buckle-detail-brown", "white-wide-leg-jeans-white-1z-duw", "brown-leather-belt-gold-buckle"],
    "2026-09-08": ["cream-v-neck-tie-back-top", "floral-embroidered-flare-jeans", "cream-brown-ny-baseball-cap", "dark-brown-leather-buckle-tote", "burgundy-patent-ballet-flats"],
    "2026-09-09": ["white-striped-crop-tee", "black-denim-mini-skirt", "brown-leather-belt-gold-buckle"],
    "2026-09-10": ["navy-square-neck-tank-top", "cream-wide-leg-drawstring-pants", "navy-longchamp-tote", "cream-black-mary-jane-flats"],
    "2026-09-11": ["navy-polka-dot-one-shoulder-top", "black-denim-mini-skirt", "cream-brown-ny-baseball-cap", "cream-black-mary-jane-flats"],
    "2026-09-12": ["asymmetric-long-sleeve-top-navy", "agolde-light-blue-denim-mini-skirt", "brown-leather-belt-gold-buckle", "navy-longchamp-tote", "adidas-spezial-navy-sneakers"],
    "2026-09-13": ["cream-polka-dot-bandeau-top", "espresso-wide-leg-jeans", "dark-brown-leather-buckle-tote"],
    "2026-09-14": ["navy-polka-dot-one-shoulder-top", "black-denim-mini-skirt"],
    "2026-09-15": ["navy-square-neck-tank-top", "agolde-light-blue-denim-mini-skirt", "navy-longchamp-tote", "adidas-spezial-navy-sneakers"],
    "2026-09-16": ["white-striped-crop-tee", "cream-wide-leg-drawstring-pants", "brown-leather-belt-gold-buckle", "dark-brown-leather-buckle-tote", "cream-black-mary-jane-flats"],
    "2026-09-17": ["asymmetric-long-sleeve-top-navy", "agolde-light-blue-denim-mini-skirt", "navy-longchamp-tote", "adidas-spezial-navy-sneakers"],
    "2026-09-18": ["cream-polka-dot-bandeau-top", "espresso-wide-leg-jeans"],
    "2026-09-19": ["cream-v-neck-tie-back-top", "floral-embroidered-flare-jeans", "cream-brown-ny-baseball-cap", "dark-brown-leather-buckle-tote", "burgundy-patent-ballet-flats"],
}
OUTFIT_LOG_TEXT = {
    "2026-09-03": ["Cream floral embroidered top"],
    "2026-09-06": ["Black crossbody bag"],
    "2026-09-07": ["Brown shoulder bag", "Black and white canvas sneakers"],
    "2026-09-09": ["Black shoulder bag", "Black high-top sneakers"],
    "2026-09-11": ["Small black shoulder bag"],
    "2026-09-13": ["Black and white canvas sneakers"],
    "2026-09-14": ["Black high-top sneakers"],
    "2026-09-18": ["Black shoulder bag", "Black and white canvas sneakers"],
}


def extract_json(raw: str) -> dict:
    """Gemini sometimes wraps JSON in prose or code fences - pull the object out."""
    raw = raw.strip()
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
    if fence:
        raw = fence.group(1)
    else:
        brace = re.search(r"\{.*\}", raw, re.DOTALL)
        if brace:
            raw = brace.group(0)
    return json.loads(raw)


def _classify_result(image_bytes: bytes, mime_type: str) -> dict:
    response = _client.models.generate_content(
        model=GEMINI_MODEL,
        contents=[types.Part.from_bytes(data=image_bytes, mime_type=mime_type), CLASSIFY_PROMPT],
        config=types.GenerateContentConfig(response_mime_type="application/json"),
    )
    return extract_json(response.text)


def _drive_service():
    creds = None
    if os.path.exists(TOKEN_PATH):
        creds = Credentials.from_authorized_user_file(TOKEN_PATH, DRIVE_SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(GoogleAuthRequest())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(CLIENT_SECRET_PATH, DRIVE_SCOPES)
            creds = flow.run_local_server(port=0)
        with open(TOKEN_PATH, "w") as fh:
            fh.write(creds.to_json())
    return build("drive", "v3", credentials=creds)


def _list_children(service, parent_id: str) -> list:
    files, page_token = [], None
    query = f"'{parent_id}' in parents and trashed = false"
    while True:
        resp = (
            service.files()
            .list(q=query, fields="nextPageToken, files(id, name, mimeType)", pageToken=page_token)
            .execute()
        )
        files.extend(resp.get("files", []))
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return files


def list_wardrobe_photos(service, folder_id: str) -> list:
    """Same folder-structure trick as mcp_servers/drive_photos_server.py:
    category/season come from which Drive subfolder a photo is filed
    under, matched by filename (each photo is a separate upload per
    folder, not one file with two parents)."""
    subfolders = {f["name"]: f["id"] for f in _list_children(service, folder_id) if f["mimeType"] == "application/vnd.google-apps.folder"}
    category_folders, season_folders = {}, {}
    for name, fid in subfolders.items():
        lower = name.lower()
        if "top" in lower:
            category_folders[fid] = "top"
        elif "bottom" in lower:
            category_folders[fid] = "bottom"
        elif "summer" in lower:
            season_folders[fid] = "summer"
        elif "winter" in lower:
            season_folders[fid] = "winter"

    season_by_name = {}
    for sfid, season in season_folders.items():
        for f in _list_children(service, sfid):
            season_by_name[f["name"]] = season

    photos = []
    for cfid, category in category_folders.items():
        for f in _list_children(service, cfid):
            if not f["mimeType"].startswith("image/"):
                continue
            photos.append({
                "file_id": f["id"],
                "name": f["name"],
                "category": category,
                "season": season_by_name.get(f["name"], "unknown"),
            })
    return photos


def _download_bytes(service, file_id: str) -> bytes:
    request_ = service.files().get_media(fileId=file_id)
    buf = io.BytesIO()
    downloader = MediaIoBaseDownload(buf, request_)
    done = False
    while not done:
        _, done = downloader.next_chunk()
    return buf.getvalue()


def _slugify(*parts) -> str:
    s = "-".join(parts).lower()
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-")


def _load_cache() -> dict:
    if os.path.exists(CACHE_PATH):
        try:
            with open(CACHE_PATH) as fh:
                return json.load(fh)
        except Exception:
            return {}
    return {}


def _save_cache(cache: dict):
    with open(CACHE_PATH, "w") as fh:
        json.dump(cache, fh)


@app.route("/")
def index():
    return send_from_directory(APP_DIR, "index.html")


@app.route("/fonts/<path:filename>")
def fonts(filename):
    """Self-hosted Satoshi font files, so the app doesn't depend on Fontshare's
    CDN being reachable (it was silently falling back to a system font when
    that CDN got blocked, making buttons render bold-and-cramped)."""
    return send_from_directory(os.path.join(APP_DIR, "fonts"), filename)


@app.route("/branding/<path:filename>")
def branding(filename):
    """Logo and other brand assets."""
    return send_from_directory(os.path.join(APP_DIR, "branding"), filename)


@app.route("/api/classify", methods=["POST"])
def classify():
    if "photo" not in request.files:
        return jsonify({"error": "no photo uploaded"}), 400

    file = request.files["photo"]
    image_bytes = file.read()
    mime_type = file.mimetype or "image/jpeg"

    try:
        result = _classify_result(image_bytes, mime_type)
    except genai_errors.ClientError as exc:
        if getattr(exc, "code", None) == 429:
            return jsonify({"error": "Gemini's free-tier quota is exhausted right now - try again later"}), 429
        return jsonify({"error": str(exc)}), 502
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500

    category = result.get("category") if result.get("category") in CATEGORIES else "top"
    season = result.get("season") if result.get("season") in ("summer", "winter") else "summer"
    return jsonify({
        "category": category,
        "type": str(result.get("type", "item"))[:60],
        "color": str(result.get("color", "unspecified"))[:40],
        "season": season,
    })


@app.route("/api/drive/catalog")
def drive_catalog():
    """Cached wardrobe from the last sync - no Drive/Gemini calls, just reads the local cache."""
    return jsonify({"catalog": list(_load_cache().values())})


@app.route("/api/outfit-log")
def outfit_log():
    """Manually logged past outfits - real photos for specific dates, stored
    as files in logged_outfits/<date>.<ext>, not synced from Drive."""
    log = {}
    if os.path.isdir(OUTFIT_LOG_DIR):
        for fname in sorted(os.listdir(OUTFIT_LOG_DIR)):
            date_str, ext = os.path.splitext(fname)
            ext = ext.lower().lstrip(".")
            if ext not in ("jpg", "jpeg", "png"):
                continue
            with open(os.path.join(OUTFIT_LOG_DIR, fname), "rb") as fh:
                b64 = base64.b64encode(fh.read()).decode()
            mime = "image/png" if ext == "png" else "image/jpeg"
            log[date_str] = {
                "photo": f"data:{mime};base64,{b64}",
                "pieces": OUTFIT_LOG_PIECES.get(date_str, []),
                "pieces_text": OUTFIT_LOG_TEXT.get(date_str, []),
            }
    return jsonify({"log": log})


@app.route("/api/drive/sync", methods=["POST"])
def drive_sync():
    """Re-scan the Drive wardrobe folder. Classifies only photos not already
    cached, so repeated syncs are cheap and don't re-spend Gemini quota."""
    try:
        service = _drive_service()
        photos = list_wardrobe_photos(service, DRIVE_FOLDER_ID)
    except Exception as exc:
        return jsonify({"error": f"Could not reach Google Drive: {exc}"}), 502

    cache = _load_cache()
    newly_classified = 0
    quota_hit = False

    for p in photos:
        if p["file_id"] in cache:
            continue
        try:
            image_bytes = _download_bytes(service, p["file_id"])
        except Exception:
            continue  # skip this one, retry on next sync

        ext = p["name"].rsplit(".", 1)[-1].lower() if "." in p["name"] else "jpg"
        mime_type = "image/png" if ext == "png" else "image/jpeg"
        photo_data_url = f"data:{mime_type};base64,{base64.b64encode(image_bytes).decode()}"

        try:
            result = _classify_result(image_bytes, mime_type)
            item_type = str(result.get("type", "item"))[:60]
            color = str(result.get("color", "unspecified"))[:40]
        except genai_errors.ClientError as exc:
            if getattr(exc, "code", None) == 429:
                quota_hit = True
                break
            item_type, color = "item", "unspecified"
        except Exception:
            item_type, color = "item", "unspecified"

        category = p["category"] if p["category"] in ("top", "bottom") else "top"
        season = p["season"] if p["season"] in ("summer", "winter") else "summer"
        item_id = _slugify(item_type, color, p["file_id"][:6])

        cache[p["file_id"]] = {
            "item_id": item_id,
            "category": category,
            "type": item_type,
            "color": color,
            "secondary_colors": [],
            "pattern": "solid",
            "season": [season],
            "occasion": ["casual"],
            "style_tags": [item_type],
            "confidence": 1,
            "notes": "synced from Google Drive",
            "photo": photo_data_url,
            "drive_file_id": p["file_id"],
            "drive_name": p["name"],
        }
        newly_classified += 1
        _save_cache(cache)  # incremental, so a quota-cutoff mid-sync doesn't lose progress

    return jsonify({
        "catalog": list(cache.values()),
        "newly_classified": newly_classified,
        "total_in_drive": len(photos),
        "quota_hit": quota_hit,
    })


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5050))
    print(f"Capsule Week running at http://localhost:{port}")
    app.run(host="0.0.0.0", port=port, debug=False)
