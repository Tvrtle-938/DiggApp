"""DiggApp — interface web de la collection.

Serveur Flask indépendant du bot Telegram (les deux tournent en parallèle).
Lancement : venv/bin/python web.py  →  http://<ip-du-mac>:5000
"""

import json
import logging
import sqlite3
from pathlib import Path

from flask import Flask, jsonify, render_template, request, send_from_directory

import search_engine

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

app = Flask(__name__)

ASSETS_DIR = Path("assets")


def serialize(row: dict) -> dict:
    """Prépare une ligne de la base pour le JSON : tags décodés, chemins web."""
    tags = []
    if row.get("tags"):
        try:
            tags = json.loads(row["tags"])
        except (json.JSONDecodeError, TypeError):
            tags = []
    return {
        "id": row["id"],
        "created_at": row["created_at"],
        "item_type": row.get("item_type") or "image",
        "category": row.get("category"),
        "title": row.get("title"),
        "description": row.get("description"),
        "tags": tags,
        "url": row.get("url"),
        "platform": row.get("platform"),
        # Chemins relatifs servis par les routes /data/… ci-dessous
        "image": row.get("file_path") if (row.get("item_type") or "image") == "image" else row.get("thumbnail_path"),
    }


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/items")
def api_items():
    """Liste des éléments, filtrable par ?category=…&type=…&search=…"""
    category = request.args.get("category")
    item_type = request.args.get("type")
    search = request.args.get("search", "").strip()

    filters, params = [], []
    if category:
        filters.append("category = ?")
        params.append(category)
    if item_type:
        filters.append("item_type = ?")
        params.append(item_type)
    if search:
        words = [w for w in search.split() if len(w) > 1] or [search]
        clause, kw_params = search_engine._keyword_clause(words)
        filters.append(clause)
        params.extend(kw_params)

    rows = search_engine._query_captures(" AND ".join(filters), params)

    # Compteurs globaux (indépendants des filtres) pour les chips et le header
    stats = search_engine.db_stats()
    counts = {
        "total": sum(n for _, n in stats["by_type"]),
        "by_category": {cat: n for cat, n in stats["by_category"]},
        "by_type": {t: n for t, n in stats["by_type"]},
    }

    return jsonify({"items": [serialize(r) for r in rows], "counts": counts})


@app.route("/api/chat", methods=["POST"])
def api_chat():
    """Chat IA : même pipeline intention → recherche → synthèse que le bot Telegram."""
    data = request.get_json(silent=True) or {}
    message = (data.get("message") or "").strip()
    if not message:
        return jsonify({"response": "Pose-moi une question sur ta collection !", "items": []})

    result = search_engine.chat(message)
    return jsonify({
        "response": result["response"],
        "items": [serialize(r) for r in result["items"]],
    })


# --- Fichiers : images, miniatures et assets (logo) ---

@app.route("/data/captures/<path:filename>")
def serve_capture(filename):
    return send_from_directory("data/captures", filename)


@app.route("/data/thumbnails/<path:filename>")
def serve_thumbnail(filename):
    return send_from_directory("data/thumbnails", filename)


@app.route("/assets/<path:filename>")
def serve_asset(filename):
    return send_from_directory("assets", filename)


if __name__ == "__main__":
    import os
    ASSETS_DIR.mkdir(exist_ok=True)
    # Port 5001 par défaut : macOS occupe le 5000 avec AirPlay Receiver.
    # Pour utiliser le 5000 : désactiver AirPlay Receiver (Réglages > Général
    # > AirDrop et Handoff) puis lancer avec PORT=5000
    port = int(os.getenv("PORT", "5001"))
    # 0.0.0.0 : accessible depuis le téléphone sur le même Wi-Fi
    app.run(host="0.0.0.0", port=port, debug=False)
