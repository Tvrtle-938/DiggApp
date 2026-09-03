"""DiggApp — interface web de la collection.

Serveur Flask indépendant du bot Telegram (les deux tournent en parallèle).
Lancement : venv/bin/python web.py  →  http://<ip-du-mac>:5000
"""

import json
import logging
import sqlite3
from pathlib import Path

from flask import Flask, jsonify, render_template, request, send_from_directory

import agent
import ai_engine
import content_studio
import embeddings
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


@app.route("/api/items/<int:item_id>", methods=["PUT"])
def api_items_update(item_id):
    """Reclassement manuel d'un élément : change la catégorie puis régénère
    l'embedding (le texte indexé commence par la catégorie, sans cela la
    recherche sémantique resterait calée sur l'ancienne)."""
    data = request.get_json(silent=True) or {}
    category = (data.get("category") or "").strip().lower()
    if category not in ai_engine.VALID_CATEGORIES:
        return jsonify({"error": f"Catégorie invalide. Valides : {', '.join(ai_engine.VALID_CATEGORIES)}"}), 400

    conn = sqlite3.connect("hub.db")
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT description, tags, user_note FROM captures WHERE id = ?",
                       (item_id,)).fetchone()
    if row is None:
        conn.close()
        return jsonify({"error": "Élément introuvable."}), 404

    conn.execute("UPDATE captures SET category = ? WHERE id = ?", (category, item_id))
    conn.commit()

    # Jamais bloquant : si Ollama est indisponible, la catégorie est quand même
    # mise à jour, l'embedding sera simplement régénéré au prochain reprocess.
    text = embeddings.build_item_text(category, row["description"], row["tags"], row["user_note"])
    vector = embeddings.get_embedding(text)
    if vector:
        conn.execute("UPDATE captures SET embedding = ? WHERE id = ?",
                     (embeddings.serialize(vector), item_id))
        conn.commit()
    else:
        logger.warning("Reclassement #%s : embedding non régénéré (Ollama indisponible ?)", item_id)
    conn.close()

    logger.info("Élément #%s reclassé en « %s » (embedding %s)",
                item_id, category, "régénéré" if vector else "inchangé")
    return jsonify({"ok": True, "id": item_id, "category": category,
                    "embedding_updated": bool(vector)})


@app.route("/api/chat", methods=["POST"])
def api_chat():
    """Chat IA : même boucle d'agent avec outils que le bot Telegram."""
    data = request.get_json(silent=True) or {}
    message = (data.get("message") or "").strip()
    if not message:
        return jsonify({"response": "Pose-moi une question sur ta collection !", "items": []})

    result = agent.chat(message)
    return jsonify({
        "response": result["response"],
        "items": [serialize(r) for r in result["items"]],
    })


# --- Studio de contenu : génération et gestion des brouillons de posts ---

@app.route("/api/targets")
def api_targets():
    """Cibles disponibles pour le Studio, selon le type de contenu (?type=post|script|ai_prompt)."""
    content_type = request.args.get("type", "post")
    if content_type == "script":
        targets = content_studio.script_format_list()
    elif content_type == "ai_prompt":
        targets = content_studio.asset_type_list()
    else:
        targets = content_studio.channel_list()
    return jsonify({"targets": targets})


@app.route("/api/drafts", methods=["GET"])
def api_drafts_list():
    status = request.args.get("status")
    return jsonify({"drafts": content_studio.list_drafts(status)})


@app.route("/api/drafts", methods=["POST"])
def api_drafts_generate():
    data = request.get_json(silent=True) or {}
    topic = (data.get("topic") or "").strip()
    content_type = (data.get("content_type") or "post").strip().lower()
    target = (data.get("target") or "").strip()
    if not topic or not target:
        return jsonify({"error": "Sujet et cible requis."}), 400
    result = content_studio.generate_draft(topic, target, content_type=content_type)
    return jsonify(result), (200 if "error" not in result else 422)


@app.route("/api/drafts/<int:draft_id>", methods=["PUT"])
def api_drafts_update(draft_id):
    data = request.get_json(silent=True) or {}
    ok = content_studio.update_draft(draft_id, content=data.get("content"), status=data.get("status"))
    return jsonify({"ok": ok})


@app.route("/api/drafts/<int:draft_id>", methods=["DELETE"])
def api_drafts_delete(draft_id):
    ok = content_studio.delete_draft(draft_id)
    return jsonify({"ok": ok})


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
