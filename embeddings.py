"""Embeddings sémantiques via Ollama (nomic-embed-text).

get_embedding(text, kind) renvoie un vecteur (liste de floats) ou None si
Ollama est indisponible. nomic-embed-text est asymétrique : les documents
stockés et les requêtes de recherche doivent être préfixés différemment
pour de meilleurs résultats.
"""

import os
import json
import logging
from typing import Optional

import requests

logger = logging.getLogger(__name__)

OLLAMA_BASE = os.getenv("OLLAMA_BASE", "http://localhost:11434")
EMBED_MODEL = "nomic-embed-text"
EMBED_TIMEOUT = 30  # secondes

_PREFIXES = {"document": "search_document: ", "query": "search_query: "}


def get_embedding(text: str, kind: str = "document") -> Optional[list]:
    """Calcule l'embedding d'un texte. kind = "document" (stockage) ou "query" (recherche)."""
    text = (text or "").strip()
    if not text:
        return None
    try:
        resp = requests.post(
            f"{OLLAMA_BASE}/api/embeddings",
            json={"model": EMBED_MODEL, "prompt": _PREFIXES.get(kind, "") + text},
            timeout=EMBED_TIMEOUT,
        )
        resp.raise_for_status()
        embedding = resp.json().get("embedding")
        return embedding if embedding else None
    except Exception as e:
        logger.warning("Embedding indisponible (%s)", e)
        return None


def build_item_text(category: Optional[str], description: Optional[str],
                    tags, user_note: Optional[str]) -> str:
    """Texte combiné utilisé pour l'embedding d'un élément de la collection.

    tags accepte une liste ou la chaîne JSON stockée en base.
    """
    if isinstance(tags, str):
        try:
            tags = json.loads(tags)
        except (json.JSONDecodeError, TypeError):
            tags = [tags]
    parts = [category, description, " ".join(tags or []), user_note]
    return " | ".join(p for p in parts if p and str(p).strip())


def serialize(vector: Optional[list]) -> Optional[str]:
    """Vecteur → JSON pour la colonne TEXT de la base."""
    return json.dumps(vector) if vector else None
