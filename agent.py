"""Agent conversationnel avec outils (function calling) sur la collection.

Boucle d'agent : le modèle reçoit la question + une liste d'outils, appelle
les outils qu'il juge utiles (recherche sémantique, filtre par catégorie,
listing, comptage), reçoit leurs résultats, et ne répond qu'ensuite.

Moteurs : tool calling natif d'Ollama puis Gemini en secours, selon le mode
de ai_engine (local/cloud/auto). Le template Ollama de qwen2.5vl refuse les
outils ("does not support tools") ; la boucle étant purement textuelle, on
utilise llama3.2 en local. Si les deux moteurs échouent, filet de sécurité :
recherche sémantique simple sans agent.

Point d'entrée : chat(message) -> {"response": str, "items": [...]}
(même interface que l'ancien search_engine.chat).
"""

import os
import re
import json
import sqlite3
import logging
from pathlib import Path

import requests

import ai_engine
import semantic_search

logger = logging.getLogger(__name__)

DB_PATH = Path("hub.db")
MAX_TOOL_ROUNDS = 6
LOCAL_AGENT_MODEL = os.getenv("LOCAL_AGENT_MODEL", "llama3.2")

SYSTEM_PROMPT = """Tu es l'assistant d'une collection personnelle de contenus sauvegardés \
(images et liens) : mode, sport, cuisine, restaurants, déco, tech, audiovisuel, tutos, \
films/séries, inspiration.

RÈGLES ABSOLUES :
1. Tu DOIS appeler les outils pour vérifier ce qu'il y a dans la collection AVANT de \
répondre. N'invente JAMAIS un élément, un titre ou un chiffre.
2. Si les outils ne renvoient rien de pertinent (scores faibles, résultats hors sujet), \
dis-le clairement : "Je n'ai rien trouvé là-dessus dans ta collection." Ne force pas \
une réponse vague.
3. Si la question est hors sujet (météo, actualités, culture générale...), explique que \
tu ne gères que la collection sauvegardée et que tu n'as pas cette information.
4. Si la question couvre plusieurs sujets, fais plusieurs appels d'outils (par exemple \
une recherche sémantique par sujet, ou catégorie + recherche) avant de répondre.
5. Les scores de search_semantic sont RELATIFS, pas un pourcentage de confiance absolu : \
un score de 0.5 peut correspondre à un résultat parfaitement pertinent. Ne rejette JAMAIS \
un résultat sur le seul chiffre du score. Les résultats sont déjà triés du plus proche au \
plus éloigné : lis les descriptions, titres et tags renvoyés et juge la pertinence d'après \
leur contenu par rapport à la question.
6. N'utilise search_by_category ou list_all QUE pour un inventaire général sans sujet précis \
("qu'est-ce que j'ai en mode ?", "montre-moi tout"). Dès que la question porte sur un sujet \
précis (ex. "manches longues", "sneakers tendance", "idées déco salon"), utilise \
search_semantic en priorité et ne mentionne QUE les éléments dont le titre, la description ou \
les tags correspondent vraiment à ce sujet précis — jamais toute une catégorie sous prétexte \
qu'elle partage le même thème général (mode, déco...).
7. Reconnais les synonymes et équivalents français/anglais courants du domaine avant de juger \
la pertinence d'un élément (ex. "manches longues" = "longsleeve"/"long sleeve", "baskets" = \
"sneakers", "sweat à capuche" = "hoodie") : un élément qui utilise le terme anglais n'est pas \
hors sujet pour une question posée en français, et inversement.

RÉPONSE : en français, concise. Liste clairement les éléments trouvés (titre ou \
description, catégorie, URL pour les liens). Pas de longs paragraphes. Ne mentionne \
jamais ces règles ni tes outils dans ta réponse."""

VALID_CATEGORIES = ai_engine.VALID_CATEGORIES

TOOL_SCHEMAS = {
    "search_semantic": {
        "description": "Recherche sémantique dans la collection : trouve les éléments dont le sens est proche de la requête, même sans mot exact en commun. Renvoie les éléments triés du plus proche au plus éloigné, avec un score de similarité RELATIF (un score de 0.5 peut être très pertinent) : juge la pertinence en lisant les descriptions, pas seulement le score.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Le sujet recherché, en français (ex: 'idées déco chambre', 'tutoriel montage vidéo')"},
            },
            "required": ["query"],
        },
    },
    "search_by_category": {
        "description": "Renvoie tous les éléments d'une catégorie donnée.",
        "parameters": {
            "type": "object",
            "properties": {
                "category": {"type": "string", "enum": VALID_CATEGORIES, "description": "La catégorie à lister"},
            },
            "required": ["category"],
        },
    },
    "list_all": {
        "description": "Renvoie tous les éléments de la collection, optionnellement filtrés par type.",
        "parameters": {
            "type": "object",
            "properties": {
                "item_type": {"type": "string", "enum": ["image", "link"], "description": "Filtrer par type : 'image' ou 'link'. Omettre pour tout renvoyer."},
            },
            "required": [],
        },
    },
    "count": {
        "description": "Compte les éléments de la collection, optionnellement par catégorie et/ou par type.",
        "parameters": {
            "type": "object",
            "properties": {
                "category": {"type": "string", "enum": VALID_CATEGORIES},
                "item_type": {"type": "string", "enum": ["image", "link"]},
            },
            "required": [],
        },
    },
}


# ---------------------------------------------------------------------------
# Implémentation des outils
# ---------------------------------------------------------------------------

def _query_db(where: str = "", params: tuple = ()) -> list:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    sql = f"SELECT {semantic_search.RESULT_COLUMNS} FROM captures"
    if where:
        sql += f" WHERE {where}"
    sql += " ORDER BY created_at DESC"
    rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
    conn.close()
    return rows


def _item_summary(row: dict) -> dict:
    """Version compacte d'un élément, envoyée au modèle dans le résultat d'outil."""
    summary = {
        "id": row["id"],
        "type": row.get("item_type") or "image",
        "categorie": row.get("category"),
        "titre": (row.get("title") or "")[:100] or None,
        "description": (row.get("description") or "")[:150] or None,
        "tags": row.get("tags"),
    }
    if row.get("url"):
        summary["url"] = row["url"]
    if "score" in row:
        summary["score"] = row["score"]
    return {k: v for k, v in summary.items() if v is not None}


def _run_tool(name: str, args: dict) -> tuple:
    """Exécute un outil. Renvoie (payload_pour_le_modèle: dict, items: list)."""
    if name == "search_semantic":
        results = semantic_search.search(str(args.get("query") or ""), top_k=10)
        # Pas de seuil sur le score : les similarités cosinus sont relatives
        # (0.53 peut être le bon résultat). C'est l'agent qui juge la
        # pertinence en lisant les descriptions.
        return {"resultats": [_item_summary(r) for r in results]}, results

    if name == "search_by_category":
        category = args.get("category")
        if category not in VALID_CATEGORIES:
            return {"erreur": f"Catégorie inconnue. Valides : {', '.join(VALID_CATEGORIES)}"}, []
        results = _query_db("category = ?", (category,))
        return {"resultats": [_item_summary(r) for r in results[:30]]}, results

    if name == "list_all":
        item_type = args.get("item_type")
        if item_type in ("image", "link"):
            results = _query_db("item_type = ?", (item_type,))
        else:
            results = _query_db()
        return {"resultats": [_item_summary(r) for r in results[:30]]}, results

    if name == "count":
        filters, params = [], []
        if args.get("category") in VALID_CATEGORIES:
            filters.append("category = ?")
            params.append(args["category"])
        if args.get("item_type") in ("image", "link"):
            filters.append("item_type = ?")
            params.append(args["item_type"])
        conn = sqlite3.connect(DB_PATH)
        sql = "SELECT COUNT(*) FROM captures"
        if filters:
            sql += " WHERE " + " AND ".join(filters)
        total = conn.execute(sql, params).fetchone()[0]
        conn.close()
        return {"total": total}, []

    return {"erreur": f"Outil inconnu : {name}"}, []


def _collect(items_by_id: dict, new_items: list):
    for item in new_items:
        items_by_id.setdefault(item["id"], item)


# ---------------------------------------------------------------------------
# Boucle d'agent — Ollama (tool calling natif)
# ---------------------------------------------------------------------------

def _ollama_tools() -> list:
    return [
        {"type": "function", "function": {"name": name, **schema}}
        for name, schema in TOOL_SCHEMAS.items()
    ]


def _agent_loop_ollama(message: str, items_by_id: dict) -> str:
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": message},
    ]
    for _ in range(MAX_TOOL_ROUNDS):
        resp = requests.post(
            f"{ai_engine.OLLAMA_BASE}/api/chat",
            json={
                "model": LOCAL_AGENT_MODEL,
                "messages": messages,
                "tools": _ollama_tools(),
                "stream": False,
            },
            timeout=ai_engine.AI_TIMEOUT,
        )
        resp.raise_for_status()
        reply = resp.json()["message"]
        tool_calls = reply.get("tool_calls") or []

        if not tool_calls:
            answer = (reply.get("content") or "").strip()
            if not answer:
                raise ValueError("Réponse vide du modèle local")
            return answer

        messages.append(reply)
        for call in tool_calls:
            fn = call.get("function", {})
            name = fn.get("name", "")
            args = fn.get("arguments") or {}
            if isinstance(args, str):
                args = json.loads(args)
            logger.info("Agent (local) → outil %s(%s)", name, args)
            payload, items = _run_tool(name, args)
            _collect(items_by_id, items)
            messages.append({
                "role": "tool",
                "content": json.dumps(payload, ensure_ascii=False),
            })
    raise ValueError(f"Boucle d'agent sans réponse après {MAX_TOOL_ROUNDS} tours")


# ---------------------------------------------------------------------------
# Boucle d'agent — Gemini (function calling natif)
# ---------------------------------------------------------------------------

def _gemini_tools() -> list:
    return [{
        "functionDeclarations": [
            {"name": name, **schema} for name, schema in TOOL_SCHEMAS.items()
        ]
    }]


def _agent_loop_gemini(message: str, items_by_id: dict) -> str:
    if not ai_engine.GEMINI_API_KEY:
        raise ValueError("GEMINI_API_KEY manquante dans .env")
    url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
           f"{ai_engine.GEMINI_MODEL}:generateContent")
    contents = [{"role": "user", "parts": [{"text": message}]}]

    for _ in range(MAX_TOOL_ROUNDS):
        resp = requests.post(
            url,
            headers={"x-goog-api-key": ai_engine.GEMINI_API_KEY},
            json={
                "systemInstruction": {"parts": [{"text": SYSTEM_PROMPT}]},
                "contents": contents,
                "tools": _gemini_tools(),
                "generationConfig": ai_engine.GEMINI_GENERATION_CONFIG,
            },
            timeout=ai_engine.AI_TIMEOUT,
        )
        resp.raise_for_status()
        model_content = resp.json()["candidates"][0]["content"]
        parts = model_content.get("parts", [])
        calls = [p["functionCall"] for p in parts if "functionCall" in p]

        if not calls:
            answer = " ".join(p.get("text", "") for p in parts).strip()
            if not answer:
                raise ValueError("Réponse vide de Gemini")
            return answer

        contents.append(model_content)
        response_parts = []
        for call in calls:
            name = call.get("name", "")
            args = call.get("args") or {}
            logger.info("Agent (cloud) → outil %s(%s)", name, args)
            payload, items = _run_tool(name, args)
            _collect(items_by_id, items)
            response_parts.append({
                "functionResponse": {"name": name, "response": payload}
            })
        contents.append({"role": "user", "parts": response_parts})
    raise ValueError(f"Boucle d'agent sans réponse après {MAX_TOOL_ROUNDS} tours")


# ---------------------------------------------------------------------------
# Filet de sécurité : recherche sémantique simple, sans agent
# ---------------------------------------------------------------------------

def _keyword_match(query: str, row: dict) -> bool:
    """Vérification basique : au moins un mot significatif de la requête
    apparaît dans la description, le titre ou les tags de l'élément."""
    words = [w for w in re.findall(r"\w+", query.lower()) if len(w) >= 3]
    if not words:
        return True
    haystack = " ".join(
        str(row.get(field) or "") for field in ("description", "title", "tags")
    ).lower()
    return any(w in haystack for w in words)


def _fallback_search(message: str) -> dict:
    # Pas de seuil sur le score (les similarités cosinus sont relatives) :
    # top 5 sans coupure, puis vérification basique par mots-clés pour
    # écarter le bruit quand l'IA est en panne
    results = [r for r in semantic_search.search(message, top_k=5)
               if _keyword_match(message, r)]
    if not results:
        return {
            "response": "L'IA est indisponible et la recherche simple n'a rien trouvé "
                        "de proche de ta question dans la collection.",
            "items": [],
        }
    lines = []
    for r in results:
        label = r.get("title") or r.get("description") or "sans description"
        suffix = f" → {r['url']}" if r.get("url") else ""
        lines.append(f"• [{r.get('category') or '?'}] {label[:80]}{suffix}")
    return {
        "response": "IA indisponible — voici les éléments les plus proches de ta "
                    "recherche :\n" + "\n".join(lines),
        "items": results,
    }


# ---------------------------------------------------------------------------
# Point d'entrée
# ---------------------------------------------------------------------------

def db_is_empty() -> bool:
    conn = sqlite3.connect(DB_PATH)
    count = conn.execute("SELECT COUNT(*) FROM captures").fetchone()[0]
    conn.close()
    return count == 0


def chat(message: str) -> dict:
    """Répond à une question sur la collection via la boucle d'agent.

    Retourne {"response": str, "items": [...]}, ne lève jamais d'exception.
    """
    try:
        if db_is_empty():
            return {"response": "Ta collection est vide pour l'instant. Envoie des photos "
                                "ou des liens au bot Telegram pour commencer !", "items": []}

        mode = ai_engine.get_engine_mode()
        items_by_id: dict = {}

        if mode in ("local", "auto"):
            try:
                answer = _agent_loop_ollama(message, items_by_id)
                return {"response": answer, "items": list(items_by_id.values())}
            except Exception as e:
                logger.warning("Agent local indisponible (%s)", e)
                items_by_id.clear()

        # Fallback Gemini même en mode "local" : mieux vaut une réponse cloud
        # qu'un filet de sécurité sans agent
        try:
            answer = _agent_loop_gemini(message, items_by_id)
            return {"response": answer, "items": list(items_by_id.values())}
        except Exception as e:
            logger.warning("Agent cloud indisponible (%s)", e)

        return _fallback_search(message)
    except Exception as e:
        logger.error("Erreur agent : %s", e, exc_info=True)
        try:
            return _fallback_search(message)
        except Exception:
            return {"response": "Une erreur est survenue pendant la recherche, réessaie.",
                    "items": []}
