"""Logique de recherche partagée entre le bot Telegram (bot.py) et le web (web.py).

Pipeline : extraction d'intention par l'IA → requête SQL construite dessus →
recherche élargie si 0 résultat → synthèse en langage naturel.
"""

import sqlite3
import logging
from pathlib import Path
from typing import Optional

import ai_engine

logger = logging.getLogger(__name__)

DB_PATH = Path("hub.db")

SEARCH_COLUMNS = """id, created_at, file_path, description, category, tags,
                    item_type, url, platform, title, thumbnail_path, alt_text"""

KEYWORD_FIELDS = ("description", "category", "tags", "title", "platform", "user_note")


def _query_captures(where: str, params: list) -> list:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    sql = f"SELECT {SEARCH_COLUMNS} FROM captures"
    if where:
        sql += f" WHERE {where}"
    sql += " ORDER BY created_at DESC"
    rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
    conn.close()
    return rows


def _keyword_clause(keywords: list):
    """OR sur chaque mot-clé, cherché dans tous les champs textuels."""
    clauses, params = [], []
    for kw in keywords:
        pattern = f"%{kw}%"
        clauses.append("(" + " OR ".join(f"{f} LIKE ?" for f in KEYWORD_FIELDS) + ")")
        params.extend([pattern] * len(KEYWORD_FIELDS))
    return "(" + " OR ".join(clauses) + ")", params


def search_db(query: str, intent: Optional[dict] = None) -> list:
    """Recherche pilotée par l'intention extraite par l'IA, avec repli littéral."""
    if intent is None:
        # IA indisponible : ancien comportement (LIKE sur les mots > 2 lettres)
        words = [w.strip() for w in query.split() if len(w.strip()) > 2] or [query.strip()]
        clause, params = _keyword_clause(words)
        return _query_captures(clause, params)

    filters, params = [], []
    if intent.get("item_type"):
        filters.append("item_type = ?")
        params.append(intent["item_type"])
    if intent.get("category"):
        filters.append("category = ?")
        params.append(intent["category"])

    keywords = intent.get("keywords") or []
    if not intent.get("is_listing") and keywords:
        clause, kw_params = _keyword_clause(keywords)
        filters.append(clause)
        params.extend(kw_params)

    results = _query_captures(" AND ".join(filters), params)

    # Recherche élargie : mots-clés tronqués (racines) et sans filtre de catégorie
    if not results and keywords and not intent.get("is_listing"):
        stems = list({kw[:4] for kw in keywords if len(kw) >= 4} | set(keywords))
        clause, params = _keyword_clause(stems)
        if intent.get("item_type"):
            clause += " AND item_type = ?"
            params.append(intent["item_type"])
        results = _query_captures(clause, params)

    return results


def db_is_empty() -> bool:
    conn = sqlite3.connect(DB_PATH)
    count = conn.execute("SELECT COUNT(*) FROM captures").fetchone()[0]
    conn.close()
    return count == 0


def db_stats() -> dict:
    conn = sqlite3.connect(DB_PATH)
    by_category = conn.execute(
        "SELECT COALESCE(category, 'sans catégorie'), COUNT(*) FROM captures GROUP BY category ORDER BY COUNT(*) DESC"
    ).fetchall()
    by_type = conn.execute(
        "SELECT COALESCE(item_type, 'image'), COUNT(*) FROM captures GROUP BY item_type"
    ).fetchall()
    conn.close()
    return {"by_category": by_category, "by_type": by_type}


def summarize_results(user_query: str, results: list) -> str:
    """Demande à l'IA de formuler une réponse naturelle à partir des résultats."""
    lines = []
    for r in results:
        label = r.get("title") or r.get("description") or "sans description"
        location = r.get("url") if r.get("item_type") == "link" else r.get("file_path")
        lines.append(
            f"- [{r.get('category') or 'sans catégorie'} / {r.get('item_type') or 'image'}] "
            f"{label} (tags: {r.get('tags') or '—'}) → {location}"
        )
    prompt = f"""Tu es un assistant personnel. L'utilisateur cherche dans sa collection d'images et de liens sauvegardés.

Question de l'utilisateur : "{user_query}"

Résultats trouvés dans la base :
{chr(10).join(lines)}

Réponds en français de façon naturelle et concise. Résume ce que tu as trouvé, mentionne les catégories et descriptions pertinentes, et pour les liens indique leur URL. Ne liste pas tout mot pour mot, synthétise intelligemment."""

    answer = ai_engine.generate_text(prompt)
    if answer:
        return answer
    return f"J'ai trouvé {len(results)} résultat(s) mais l'IA est hors ligne pour les synthétiser."


def chat(message: str) -> dict:
    """Pipeline complet pour le chat : intention → recherche → synthèse.

    Retourne {"response": str, "items": [...]}, ne lève jamais d'exception.
    """
    try:
        if db_is_empty():
            return {"response": "Ta collection est vide pour l'instant. Envoie des photos ou des liens au bot Telegram pour commencer !", "items": []}

        intent = ai_engine.extract_search_intent(message)
        logger.info("Intention extraite : %s", intent)
        results = search_db(message, intent)

        if not results:
            return {"response": "Je n'ai rien trouvé pour cette recherche. Essaie avec d'autres mots-clés, ou demande-moi ce qu'il y a dans ta collection.", "items": []}

        return {"response": summarize_results(message, results), "items": results}
    except Exception as e:
        logger.error("Erreur chat : %s", e, exc_info=True)
        return {"response": "Une erreur est survenue pendant la recherche, réessaie.", "items": []}
