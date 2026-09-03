"""DiggApp — interface web de la collection.

Serveur Flask indépendant du bot Telegram (les deux tournent en parallèle).
Lancement : venv/bin/python web.py  →  http://<ip-du-mac>:5000
"""

import json
import logging
from pathlib import Path

from flask import Flask, jsonify, render_template, request, send_from_directory

import agent
import content_studio
import item_editor
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
    """Correction manuelle d'un élément : catégorie, titre, description, tags,
    et remplacement éventuel de l'image. Accepte du JSON (métadonnées seules)
    ou du multipart/form-data (métadonnées + fichier image). La logique —
    validation, régénération d'embedding non bloquante — vit dans item_editor,
    partagée avec les outils de l'agent."""
    if request.content_type and request.content_type.startswith("multipart/"):
        data = request.form
        new_image = request.files.get("image")
    else:
        data = request.get_json(silent=True) or {}
        new_image = None

    updates = {k: data.get(k) for k in ("category", "title", "description", "tags")
               if data.get(k) is not None}

    result = {"ok": True, "id": item_id, "embedding_updated": False}
    if updates:
        result = item_editor.update_item(item_id, **updates)
        if "error" in result:
            return jsonify(result), (404 if "introuvable" in result["error"] else 400)

    if new_image and new_image.filename:
        img_result = item_editor.replace_image(item_id, new_image.read(), new_image.filename)
        if "error" in img_result:
            return jsonify(img_result), (404 if "introuvable" in img_result["error"] else 400)
        result["image"] = img_result["image"]

    if not updates and not (new_image and new_image.filename):
        return jsonify({"error": "Aucun champ à mettre à jour."}), 400

    if "item" in result:
        result["item"] = serialize(result["item"])
    return jsonify(result)


@app.route("/api/items/<int:item_id>", methods=["DELETE"])
def api_items_delete(item_id):
    """Suppression définitive d'un élément (RGPD, droit à l'effacement) :
    ligne en base + fichiers image associés. Logique dans item_editor."""
    result = item_editor.delete_item(item_id)
    if "error" in result:
        return jsonify(result), (404 if "introuvable" in result["error"] else 400)
    return jsonify(result)


# --- Ligne éditoriale du Studio (cible/persona, ton, engagements RSE) ---

@app.route("/api/editorial-line", methods=["GET"])
def api_editorial_get():
    return jsonify(content_studio.get_editorial_line())


@app.route("/api/editorial-line", methods=["PUT"])
def api_editorial_put():
    data = request.get_json(silent=True) or {}
    line = content_studio.set_editorial_line(
        persona=data.get("persona"), tone=data.get("tone"), rse=data.get("rse"))
    return jsonify(line)


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
