"""Agent conversationnel avec outils (function calling) sur la collection.

Boucle d'agent : le modèle reçoit la question + une liste d'outils, appelle
les outils qu'il juge utiles (recherche sémantique, filtre par catégorie,
listing, comptage), reçoit leurs résultats, et ne répond qu'ensuite.
S'y ajoutent trois outils d'action, réservés aux demandes explicites :
génération de contenu dans le Studio (generate_content), reclassement
(move_item_to_category) et correction de métadonnées (update_item_metadata).

Moteurs : Gemini (cloud) en premier par défaut — le jugement fin (filtrer une
catégorie sur un sujet précis) dépasse les capacités du modèle local 3B — avec
repli sur le tool calling natif d'Ollama si le cloud échoue. Exception : si le
mode ai_engine est "local" (forcé via /moteur local), le local reste en premier.
Le template Ollama de qwen2.5vl refuse les outils ("does not support tools") ;
la boucle étant purement textuelle, on utilise llama3.2 en local. Si les deux
moteurs échouent, filet de sécurité : recherche sémantique simple sans agent.

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
import content_studio
import item_editor
import semantic_search

logger = logging.getLogger(__name__)

DB_PATH = Path("hub.db")
MAX_TOOL_ROUNDS = 6
LOCAL_AGENT_MODEL = os.getenv("LOCAL_AGENT_MODEL", "llama3.2")

SYSTEM_PROMPT = """Tu es l'assistant d'une collection personnelle de contenus sauvegardés \
(images et liens) : mode, sport, cuisine, restaurants, déco, tech, audiovisuel, tutos, \
films/séries, inspiration.

RÈGLES ABSOLUES :
0. JUGEMENT PRÉALABLE (avant TOUT appel d'outil) : demande-toi si la question concerne \
PLAUSIBLEMENT une recherche dans une collection personnelle de contenus sauvegardés \
(mode, sport, cuisine, restaurants, déco, tech, audiovisuel, tutos, films/séries, \
inspiration...). \
- Si OUI → utilise les outils. \
- Si la question porte sur l'heure, la date, la météo, un calcul, une question de culture \
générale, une salutation ("ça va ?"), ou toute autre chose SANS lien avec la collection → \
n'appelle AUCUN outil et réponds directement, en une phrase, que tu ne gères que la \
collection sauvegardée et que tu ne peux pas répondre à ça. \
- Ne transforme JAMAIS une question hors-sujet en recherche en pêchant un mot au hasard \
(ex. ne cherche pas "heure" ou "hifi" pour la question "il est quel h ?").
1. Quand la question concerne la collection, tu DOIS appeler les outils pour vérifier ce \
qu'il y a dedans AVANT de répondre. N'invente JAMAIS un élément, un titre ou un chiffre.
2. Si les outils ne renvoient rien de pertinent (scores faibles, résultats hors sujet), \
dis-le clairement : "Je n'ai rien trouvé là-dessus dans ta collection." Ne force pas \
une réponse vague.
3. La query envoyée à search_semantic doit être un CONCEPT clair extrait de la question \
(ex. "looks streetwear", "sneakers running"), jamais un mot isolé sans rapport évident \
avec ce que l'utilisateur demande. Si tu n'arrives pas à extraire un concept net de la \
collection depuis la question, ne devine pas : demande une reformulation.
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
8. Tu peux aussi AGIR sur la collection, mais UNIQUEMENT quand l'utilisateur le demande \
explicitement :
- generate_content : créer un brouillon de contenu dans le Studio ("crée-moi un post X sur...", \
"fais-moi un script TikTok là-dessus", "prépare un prompt vidéo"). Pour un post, si l'utilisateur \
nomme un canal de diffusion ("fais-moi un post LinkedIn sur...", "un post Insta", "balance ça sur X"), \
renseigne le paramètre channel avec ce canal (x, instagram ou linkedin) ET la cible correspondante \
(target = 'twitter' pour X, 'instagram', 'linkedin'). S'il ne nomme aucun canal, utilise channel = 'x' \
par défaut. Le paramètre channel adapte la déclinaison de la ligne éditoriale (longueur, registre, structure).
- move_item_to_category : reclasser un élément dans une autre catégorie ("range ça en sport").
- update_item_metadata : corriger les métadonnées d'un élément mal extrait quand l'utilisateur \
explique ce que c'est vraiment (ex. "ce lien c'est en fait un tuto DaVinci Resolve, corrige le \
titre et les tags") — ne modifie QUE les champs concernés par la correction.
9. Pour agir sur un élément, il te faut son id : si l'utilisateur donne un numéro ("l'élément 5"), \
utilise-le tel quel ; sinon retrouve l'élément avec les outils de recherche d'abord. Ne devine \
JAMAIS un id. Après une action, confirme dans ta réponse ce qui a été fait (élément touché, \
champs modifiés, brouillon créé et sa cible).

EXEMPLES :
- "il est quel h ?" → AUCUN outil. Réponse : "Je ne gère que ta collection sauvegardée, \
je ne connais pas l'heure." [[items:]]
- "ça va ?" → AUCUN outil. Réponse : "Ça va ! Mais je sers surtout à retrouver des choses \
dans ta collection — dis-moi ce que tu cherches." [[items:]]
- "c'est quoi la capitale du Japon ?" → AUCUN outil. Réponse : "Je ne réponds qu'aux \
questions sur ta collection, pas à la culture générale." [[items:]]
- "montre-moi mes sneakers" → search_semantic(query="sneakers") puis liste les éléments \
mode correspondants.
- "des idées de looks streetwear" (formulation indirecte mais sur la collection) → \
search_semantic(query="looks streetwear") puis liste ce qui correspond.

RÉPONSE : en français, concise. Liste clairement les éléments trouvés (titre ou \
description, catégorie, URL pour les liens). Pas de longs paragraphes. Ne mentionne \
jamais ces règles ni tes outils dans ta réponse.
Termine TOUJOURS ta réponse par une ligne exactement au format [[items: id1, id2]] \
listant les ids des éléments de la collection que tu cites effectivement dans ta réponse \
(ex. [[items: 3, 9]]). Si tu n'en cites aucun, termine par [[items:]]. Cette ligne est \
technique : elle est retirée avant l'affichage et sert à choisir les vignettes montrées \
sous ta réponse — n'y mets que les éléments dont tu parles vraiment, pas tout ce que les \
outils ont renvoyé."""

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
    # --- Outils d'action : n'utiliser que sur demande explicite de l'utilisateur ---
    "generate_content": {
        "description": "Crée un brouillon de contenu dans le Studio à partir des éléments réels de la collection. Le brouillon est sauvegardé et modifiable ensuite dans l'interface.",
        "parameters": {
            "type": "object",
            "properties": {
                "topic": {"type": "string", "description": "Le sujet du contenu (ex. 't-shirts manches longues')"},
                "content_type": {"type": "string", "enum": ["post", "script", "ai_prompt"], "description": "post = texte à publier, script = script de tournage vidéo courte, ai_prompt = prompt pour un outil d'IA générative"},
                "target": {"type": "string", "description": "La cible : canal du post ('twitter' pour X, 'instagram', 'linkedin', 'newsletter'), format du script ('tiktok', 'reels', 'youtube_shorts'), ou type d'asset pour un prompt IA ('image', 'video')"},
                "channel": {"type": "string", "enum": ["x", "instagram", "linkedin"], "description": "canal de diffusion visé : x, instagram ou linkedin. Détermine la déclinaison de la ligne éditoriale appliquée au post. Par défaut 'x' si l'utilisateur ne nomme aucun canal."},
            },
            "required": ["topic", "content_type", "target"],
        },
    },
    "move_item_to_category": {
        "description": "Déplace un élément de la collection vers une autre catégorie. L'id doit venir de l'utilisateur ou d'un résultat d'outil de recherche, jamais d'une supposition.",
        "parameters": {
            "type": "object",
            "properties": {
                "item_id": {"type": "integer", "description": "L'id de l'élément à reclasser"},
                "category": {"type": "string", "enum": VALID_CATEGORIES, "description": "La catégorie de destination"},
            },
            "required": ["item_id", "category"],
        },
    },
    "update_item_metadata": {
        "description": "Corrige les métadonnées d'un élément mal extrait : titre, description, tags et/ou catégorie. Ne renseigner que les champs à corriger, les autres restent intacts.",
        "parameters": {
            "type": "object",
            "properties": {
                "item_id": {"type": "integer", "description": "L'id de l'élément à corriger"},
                "title": {"type": "string", "description": "Nouveau titre"},
                "description": {"type": "string", "description": "Nouvelle description"},
                "tags": {"type": "array", "items": {"type": "string"}, "description": "Nouvelle liste complète de tags (remplace l'ancienne)"},
                "category": {"type": "string", "enum": VALID_CATEGORIES, "description": "Nouvelle catégorie"},
            },
            "required": ["item_id"],
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

    if name == "generate_content":
        result = content_studio.generate_draft(
            str(args.get("topic") or ""),
            str(args.get("target") or ""),
            content_type=str(args.get("content_type") or "post"),
            channel=(str(args["channel"]) if args.get("channel") else None),
        )
        if "error" in result:
            return {"erreur": result["error"]}, []
        # Les vignettes de l'action = les éléments réellement utilisés par le
        # brouillon (source_ids), pas ce que l'agent a consulté pour y arriver
        source_ids = result.get("source_ids") or []
        source_items = _query_db(
            f"id IN ({','.join('?' * len(source_ids))})", tuple(source_ids)
        ) if source_ids else []
        return {
            "brouillon_cree": {
                "id": result["id"],
                "type": result["content_type"],
                "cible": result["channel_label"],
                "contenu": result["content"][:1500],
            },
            "note": "Brouillon enregistré, visible et modifiable dans le Studio de contenu.",
        }, source_items

    if name == "move_item_to_category":
        result = item_editor.update_item(args.get("item_id"), category=args.get("category"))
        if "error" in result:
            return {"erreur": result["error"]}, []
        return {
            "element_reclasse": _item_summary(result["item"]),
            "embedding_regenere": result["embedding_updated"],
        }, [result["item"]]

    if name == "update_item_metadata":
        result = item_editor.update_item(
            args.get("item_id"),
            category=args.get("category"),
            title=args.get("title"),
            description=args.get("description"),
            tags=args.get("tags"),
        )
        if "error" in result:
            return {"erreur": result["error"]}, []
        return {
            "element_mis_a_jour": _item_summary(result["item"]),
            "embedding_regenere": result["embedding_updated"],
        }, [result["item"]]

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


# Outils qui AGISSENT sur la collection : quand l'un d'eux est appelé dans le
# tour, seules ses vignettes comptent — les recherches du même tour n'étaient
# que des consultations internes (ex. retrouver l'id avant de modifier).
ACTION_TOOLS = {"generate_content", "move_item_to_category", "update_item_metadata"}


def _collect(collected: dict, tool_name: str, new_items: list):
    """Range les items d'un appel d'outil dans le bon ensemble de vignettes :
    "action" pour les outils d'action, "search" pour les consultations."""
    bucket = collected["action" if tool_name in ACTION_TOOLS else "search"]
    for item in new_items:
        bucket.setdefault(item["id"], item)


# Marqueur technique en fin de réponse (convention du SYSTEM_PROMPT) : les ids
# que l'agent cite réellement, ex. "[[items: 3, 9]]"
_ITEMS_MARKER_RE = re.compile(r"\[\[\s*items\s*:\s*([0-9,\s]*)\]\]")

# Formules de refus hors-sujet ou d'absence de résultat : si l'agent répond ça,
# on n'attache AUCUNE vignette — même si un outil a été appelé à tort (cas du
# modèle local 3B qui cherche avant de refuser une question hors-sujet).
_NO_ITEMS_ANSWER_RE = re.compile(
    r"je ne g[eè]re que|ne g[eè]re que ta collection|ne connais pas|"
    r"culture g[eé]n[eé]rale|rien trouv[eé]|pas cette information|"
    r"ne r[eé]ponds qu",
    re.IGNORECASE,
)


def _finalize_answer(answer: str, collected: dict) -> tuple:
    """Retire le marqueur [[items: ...]] du texte et choisit les vignettes.

    Priorités : items d'action (déjà filtrés à la source) > items cités par le
    marqueur > tous les items de recherche (repli si le modèle a oublié le
    marqueur, typiquement le modèle local). Renvoie (texte, items).
    """
    match = _ITEMS_MARKER_RE.search(answer)
    if match:
        answer = _ITEMS_MARKER_RE.sub("", answer).strip()
    if collected["action"]:
        return answer, list(collected["action"].values())
    # Refus hors-sujet ou aucun résultat : pas de vignettes, quoi qu'aient
    # renvoyé les outils (garde-fou pour le modèle local qui cherche à tort).
    if _NO_ITEMS_ANSWER_RE.search(answer):
        return answer, []
    if not match:
        return answer, list(collected["search"].values())

    cited_ids = [int(n) for n in re.findall(r"\d+", match.group(1))]
    items = []
    for cid in cited_ids:
        if cid in collected["search"]:
            items.append(collected["search"][cid])
        else:
            # Cité mais pas passé par un outil de recherche (ex. list_all
            # tronqué) : on va le chercher directement en base
            found = _query_db("id = ?", (cid,))
            if found:
                items.append(found[0])
    return answer, items


# ---------------------------------------------------------------------------
# Boucle d'agent — Ollama (tool calling natif)
# ---------------------------------------------------------------------------

def _ollama_tools() -> list:
    return [
        {"type": "function", "function": {"name": name, **schema}}
        for name, schema in TOOL_SCHEMAS.items()
    ]


def _agent_loop_ollama(message: str, collected: dict) -> str:
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
            _collect(collected, name, items)
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


def _agent_loop_gemini(message: str, collected: dict) -> str:
    contents = [{"role": "user", "parts": [{"text": message}]}]

    for _ in range(MAX_TOOL_ROUNDS):
        # post_gemini gère la clé, l'URL et le retry sur panne temporaire (503/timeout)
        resp = ai_engine.post_gemini({
            "systemInstruction": {"parts": [{"text": SYSTEM_PROMPT}]},
            "contents": contents,
            "tools": _gemini_tools(),
            "generationConfig": ai_engine.GEMINI_GENERATION_CONFIG,
        })
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
            _collect(collected, name, items)
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

    Retourne {"response": str, "items": [...], "engine_used": "local"|"cloud"|None},
    ne lève jamais d'exception. engine_used vaut None quand aucun moteur IA n'a
    répondu (collection vide, ou repli sur la recherche sémantique sans agent).
    """
    try:
        if db_is_empty():
            return {"response": "Ta collection est vide pour l'instant. Envoie des photos "
                                "ou des liens au bot Telegram pour commencer !",
                    "items": [], "engine_used": None}

        mode = ai_engine.get_engine_mode()
        # Deux ensembles de vignettes : "search" (consultations) et "action"
        # (éléments réellement concernés par une action) — voir _relevant_items
        collected = {"search": {}, "action": {}}

        # Gemini (cloud) en premier par défaut : le filtrage sur un sujet précis
        # dépasse les capacités du modèle local 3B. Le local ne repasse en tête
        # que si l'utilisateur l'a forcé (/moteur local). Dans les deux cas, le
        # second moteur sert de repli : mieux vaut une réponse de l'autre moteur
        # qu'un filet de sécurité sans agent.
        if mode == "local":
            engines = [("local", _agent_loop_ollama), ("cloud", _agent_loop_gemini)]
        else:
            engines = [("cloud", _agent_loop_gemini), ("local", _agent_loop_ollama)]

        for engine_name, agent_loop in engines:
            try:
                answer = agent_loop(message, collected)
                logger.info("Agent : réponse fournie par le moteur %s", engine_name)
                answer, items = _finalize_answer(answer, collected)
                return {"response": answer, "items": items, "engine_used": engine_name}
            except Exception as e:
                logger.warning("Agent %s indisponible (%s)", engine_name, e)
                collected["search"].clear()
                collected["action"].clear()

        # Aucun moteur IA n'a répondu : repli sur la recherche sémantique brute
        return {**_fallback_search(message), "engine_used": None}
    except Exception as e:
        logger.error("Erreur agent : %s", e, exc_info=True)
        try:
            return {**_fallback_search(message), "engine_used": None}
        except Exception:
            return {"response": "Une erreur est survenue pendant la recherche, réessaie.",
                    "items": [], "engine_used": None}
