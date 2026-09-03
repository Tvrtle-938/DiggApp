"""Recherche sémantique par similarité cosinus sur les embeddings stockés.

Avec < 200 éléments, un parcours complet en numpy est instantané ;
pas besoin d'index vectoriel.
"""

import json
import sqlite3
import logging
from pathlib import Path

import numpy as np

import embeddings

logger = logging.getLogger(__name__)

DB_PATH = Path("hub.db")

RESULT_COLUMNS = """id, created_at, file_path, description, category, tags,
                    item_type, url, platform, title, thumbnail_path, user_note"""


def search(query: str, top_k: int = 10) -> list:
    """Renvoie les top_k éléments les plus proches de la question, triés par
    score décroissant, avec un champ "score" (similarité cosinus, 1.0 =
    identique). Aucun seuil de coupure : les scores sont relatifs et le
    jugement de pertinence revient à l'appelant (agent). Liste vide si
    l'embedding de la requête échoue ou si rien n'est indexé."""
    query_vec = embeddings.get_embedding(query, kind="query")
    if not query_vec:
        return []

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = [dict(r) for r in conn.execute(
        f"SELECT {RESULT_COLUMNS}, embedding FROM captures WHERE embedding IS NOT NULL"
    ).fetchall()]
    conn.close()
    if not rows:
        return []

    vectors = []
    for row in rows:
        try:
            vectors.append(json.loads(row.pop("embedding")))
        except (json.JSONDecodeError, TypeError):
            vectors.append(None)

    valid = [(row, v) for row, v in zip(rows, vectors) if v]
    if not valid:
        return []

    matrix = np.array([v for _, v in valid], dtype=np.float32)
    q = np.array(query_vec, dtype=np.float32)
    scores = (matrix @ q) / (np.linalg.norm(matrix, axis=1) * np.linalg.norm(q) + 1e-10)

    ranked = sorted(zip((row for row, _ in valid), scores), key=lambda x: -x[1])[:top_k]
    results = []
    for row, score in ranked:
        row["score"] = round(float(score), 3)
        results.append(row)
    return results
