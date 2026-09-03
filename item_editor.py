"""Mise à jour des métadonnées d'un élément de la collection.

Implémentation unique partagée entre la route web (PUT /api/items/<id>) et
les outils d'action de l'agent (move_item_to_category, update_item_metadata) :
mêmes validations, même régénération d'embedding.

L'embedding est TOUJOURS régénéré après une modification de texte (le texte
indexé commence par la catégorie et contient description + tags), mais jamais
bloquant : si Ollama est indisponible, les champs sont quand même mis à jour
et un avertissement est logué (même logique que bot.py au moment du stockage).
"""

import json
import sqlite3
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional
from uuid import uuid4

import ai_engine
import embeddings
import semantic_search

logger = logging.getLogger(__name__)

DB_PATH = Path("hub.db")

# Extensions acceptées pour le remplacement d'image (input file natif)
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}


def _normalize_tags(tags) -> Optional[list]:
    """Accepte une liste ou une chaîne "a, b, c" ; renvoie une liste propre."""
    if tags is None:
        return None
    if isinstance(tags, str):
        tags = tags.split(",")
    return [str(t).strip() for t in tags if str(t).strip()]


def _refresh_embedding(conn, item_id: int, row: dict) -> bool:
    """Régénère l'embedding d'un élément. Renvoie True si réussi."""
    text = embeddings.build_item_text(
        row.get("category"), row.get("description"), row.get("tags"), row.get("user_note"))
    vector = embeddings.get_embedding(text)
    if vector:
        conn.execute("UPDATE captures SET embedding = ? WHERE id = ?",
                     (embeddings.serialize(vector), item_id))
        conn.commit()
        return True
    logger.warning("Élément #%s : embedding non régénéré (Ollama indisponible ?)", item_id)
    return False


def update_item(item_id, category=None, title=None, description=None, tags=None) -> dict:
    """Met à jour les champs fournis (les autres restent intacts) puis régénère
    l'embedding. Renvoie {"ok", "item", "embedding_updated"} ou {"error": ...}.
    """
    try:
        item_id = int(item_id)
    except (TypeError, ValueError):
        return {"error": f"Identifiant d'élément invalide : {item_id}"}

    fields, params = [], []
    if category is not None:
        category = str(category).strip().lower()
        if category not in ai_engine.VALID_CATEGORIES:
            return {"error": f"Catégorie invalide. Valides : {', '.join(ai_engine.VALID_CATEGORIES)}"}
        fields.append("category = ?")
        params.append(category)
    if title is not None:
        fields.append("title = ?")
        params.append(str(title).strip() or None)
    if description is not None:
        fields.append("description = ?")
        params.append(str(description).strip() or None)
    norm_tags = _normalize_tags(tags)
    if norm_tags is not None:
        fields.append("tags = ?")
        params.append(json.dumps(norm_tags, ensure_ascii=False))
    if not fields:
        return {"error": "Aucun champ à mettre à jour."}

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    if conn.execute("SELECT id FROM captures WHERE id = ?", (item_id,)).fetchone() is None:
        conn.close()
        return {"error": f"Élément #{item_id} introuvable."}

    params.append(item_id)
    conn.execute(f"UPDATE captures SET {', '.join(fields)} WHERE id = ?", params)
    conn.commit()

    row = dict(conn.execute(
        f"SELECT {semantic_search.RESULT_COLUMNS} FROM captures WHERE id = ?",
        (item_id,)).fetchone())
    embedding_ok = _refresh_embedding(conn, item_id, row)
    conn.close()

    logger.info("Élément #%s mis à jour (%s) — embedding %s", item_id,
                ", ".join(f.split(" ")[0] for f in fields),
                "régénéré" if embedding_ok else "inchangé")
    return {"ok": True, "item": row, "embedding_updated": embedding_ok}


def replace_image(item_id, file_bytes: bytes, original_filename: str) -> dict:
    """Remplace l'image d'un élément : capture pour une image, miniature pour
    un lien. Le nouveau fichier suit la convention de nommage du bot, l'ancien
    est supprimé (au mieux : son absence n'est jamais une erreur).
    Pas de régénération d'embedding : l'image ne participe pas au texte indexé.
    """
    try:
        item_id = int(item_id)
    except (TypeError, ValueError):
        return {"error": f"Identifiant d'élément invalide : {item_id}"}

    ext = Path(original_filename or "").suffix.lower()
    if ext not in IMAGE_EXTENSIONS:
        return {"error": "Format d'image non pris en charge (jpg, jpeg, png, webp, gif)."}
    if not file_bytes:
        return {"error": "Fichier image vide."}

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT item_type, file_path, thumbnail_path FROM captures WHERE id = ?",
                       (item_id,)).fetchone()
    if row is None:
        conn.close()
        return {"error": f"Élément #{item_id} introuvable."}

    is_image = (row["item_type"] or "image") == "image"
    column = "file_path" if is_image else "thumbnail_path"
    dest_dir = Path("data/captures") if is_image else Path("data/thumbnails")
    dest_dir.mkdir(parents=True, exist_ok=True)

    # Même convention de nommage que les captures du bot
    name = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid4().hex[:12]}{ext}"
    new_path = dest_dir / name
    new_path.write_bytes(file_bytes)

    old_path = row[column]
    conn.execute(f"UPDATE captures SET {column} = ? WHERE id = ?",
                 (new_path.as_posix(), item_id))
    conn.commit()
    conn.close()

    if old_path:
        try:
            Path(old_path).unlink(missing_ok=True)
        except OSError as e:
            logger.warning("Ancienne image de #%s non supprimée (%s)", item_id, e)

    logger.info("Élément #%s : image remplacée → %s", item_id, new_path)
    return {"ok": True, "image": new_path.as_posix()}
