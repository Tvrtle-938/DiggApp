"""Studio de contenu : posts, scripts de tournage et prompts IA générative,
générés à partir de la collection DiggApp et adaptés à une cible de diffusion.

Trois types de contenu (content_type) :
- "post"      : texte prêt à publier sur un réseau/canal (contrainte de longueur
                réellement appliquée en Python, jamais laissée à l'IA).
- "script"    : script de tournage structuré (accroche, plans, CTA) pour que
                l'utilisateur filme lui-même une courte vidéo (TikTok/Reels/Shorts).
- "ai_prompt" : prompt prêt à coller dans un outil de génération IA externe
                (image ou vidéo — Kling, Higgsfield, Grok Imagine, Midjourney...).
                DiggApp ne génère PAS l'image/vidéo elle-même : elle prépare le
                prompt à partir des données réelles de la collection, l'utilisateur
                le fait ensuite tourner dans l'outil de son choix.

Comme l'agent de recherche (agent.py), la génération de contenu passe par le
cloud (Gemini) en priorité : la qualité rédactionnelle et le respect fin des
contraintes comptent plus que la latence. Repli automatique sur le modèle
local (llama3.2) si le cloud échoue.

Point d'entrée principal : generate_draft(topic, target, content_type="post") -> dict
Gestion : list_drafts(), update_draft(), delete_draft()
Listes pour l'UI : channel_list(), script_format_list(), asset_type_list()
"""

import os
import re
import json
import sqlite3
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import requests

import ai_engine
import semantic_search

logger = logging.getLogger(__name__)

DB_PATH = Path("hub.db")

# Même modèle texte que l'agent conversationnel (agent.py) : qwen2.5vl ne gère
# pas bien la génération de texte libre sans image, llama3.2 est plus adapté.
LOCAL_CONTENT_MODEL = os.getenv("LOCAL_AGENT_MODEL", "llama3.2")

CONTENT_TYPES = ("post", "script", "ai_prompt")

DEFAULT_PERSONA = (
    "un public jeune et connecté, passionné de mode/tendances/lifestyle, qui suit des "
    "contenus de curation pour repérer les bons plans et objets tendance sans tomber "
    "dans la surconsommation"
)

# ---------------------------------------------------------------------------
# Ligne éditoriale paramétrable (cible/persona, ton, engagements RSE) —
# stockée dans une table settings clé/valeur, DEFAULT_PERSONA en défaut tant
# que rien n'est enregistré. Injectée dans les trois constructeurs de prompt.
# ---------------------------------------------------------------------------

EDITORIAL_DEFAULTS = {"persona": DEFAULT_PERSONA, "tone": "", "rse": ""}
_EDITORIAL_KEY = "editorial_line"


def _ensure_settings_table():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)")
    conn.commit()
    conn.close()


def get_editorial_line() -> dict:
    """Ligne éditoriale enregistrée, complétée par les défauts champ par champ."""
    _ensure_settings_table()
    conn = sqlite3.connect(DB_PATH)
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (_EDITORIAL_KEY,)).fetchone()
    conn.close()
    saved = {}
    if row:
        try:
            saved = json.loads(row[0]) or {}
        except (json.JSONDecodeError, TypeError):
            saved = {}
    line = dict(EDITORIAL_DEFAULTS)
    line.update({k: str(v).strip() for k, v in saved.items() if k in EDITORIAL_DEFAULTS})
    # Une persona vidée retombe sur le défaut : les prompts en ont toujours besoin
    if not line["persona"]:
        line["persona"] = DEFAULT_PERSONA
    return line


def set_editorial_line(persona=None, tone=None, rse=None) -> dict:
    """Met à jour les champs fournis (None = inchangé) et renvoie la ligne complète."""
    line = get_editorial_line()
    for key, value in (("persona", persona), ("tone", tone), ("rse", rse)):
        if value is not None:
            line[key] = str(value).strip()
    _ensure_settings_table()
    conn = sqlite3.connect(DB_PATH)
    conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                 (_EDITORIAL_KEY, json.dumps(line, ensure_ascii=False)))
    conn.commit()
    conn.close()
    return get_editorial_line()


def _editorial_block() -> str:
    """Bloc "ligne éditoriale" injecté en tête des trois prompts."""
    line = get_editorial_line()
    parts = [f"Ta cible : {line['persona']}."]
    if line["tone"]:
        parts.append(f"Ton éditorial à respecter : {line['tone']}.")
    if line["rse"]:
        parts.append(f"Engagements éditoriaux (RSE) à respecter impérativement : {line['rse']}.")
    return "\n".join(parts)

# ---------------------------------------------------------------------------
# Cibles par type de contenu — contraintes réellement appliquées quand elles
# existent (longueur de post), jamais seulement suggérées au modèle.
# ---------------------------------------------------------------------------
CHANNELS = {
    "twitter": {
        "label": "Tweet / X",
        "max_chars": 280,
        "style": "punchy et direct, 0 à 2 hashtags maximum, emojis avec parcimonie",
    },
    "instagram": {
        "label": "Légende Instagram",
        "max_chars": 2200,
        "style": "chaleureux et visuel, bloc de 5 à 10 hashtags pertinents en fin de légende, emojis bienvenus",
    },
    "linkedin": {
        "label": "Post LinkedIn",
        "max_chars": 3000,
        "style": "professionnel mais humain, ton storytelling, 2 à 3 hashtags maximum, pas d'emoji excessif",
    },
    "newsletter": {
        "label": "Bloc newsletter",
        "max_chars": 1200,
        "style": "ton chaleureux et personnel, style édito court, aucun hashtag",
    },
}

SCRIPT_FORMATS = {
    "tiktok": {
        "label": "Script TikTok",
        "duree": "15 à 30 secondes",
        "style": "rythme rapide, accroche dans les 2 premières secondes, texte à l'écran lisible, CTA clair en fin de vidéo",
    },
    "reels": {
        "label": "Script Instagram Reels",
        "duree": "15 à 30 secondes",
        "style": "rythme rapide, accroche dans les 2 premières secondes, texte à l'écran lisible, CTA clair en fin de vidéo",
    },
    "youtube_shorts": {
        "label": "Script YouTube Shorts",
        "duree": "30 à 60 secondes",
        "style": "accroche immédiate, peut développer un peu plus l'argumentaire qu'un TikTok",
    },
}

ASSET_TYPES = {
    "image": {
        "label": "Prompt image (Midjourney, Nano Banana...)",
        "guidance": "décris précisément le sujet, le style visuel, le cadrage, la lumière et "
                    "l'ambiance ; précise un ratio adapté au canal visé",
    },
    "video": {
        "label": "Prompt vidéo (Kling, Higgsfield, Grok Imagine...)",
        "guidance": "décris le sujet, le mouvement de caméra, l'action, le style visuel, "
                    "l'ambiance et une durée approximative ; précise un ratio vertical pour du contenu social",
    },
}


def channel_list() -> list:
    return [{"key": k, **v} for k, v in CHANNELS.items()]


def script_format_list() -> list:
    return [{"key": k, **v} for k, v in SCRIPT_FORMATS.items()]


def asset_type_list() -> list:
    return [{"key": k, **v} for k, v in ASSET_TYPES.items()]


def _channel_config(channel: str) -> dict:
    key = (channel or "").strip().lower()
    if key in CHANNELS:
        return {"key": key, **CHANNELS[key]}
    # Canal libre/inconnu : pas de limite dure, l'IA adapte le ton au nom du
    # canal tel quel (répond au besoin "n'importe quel canal" — exposé via
    # la commande Telegram /post, le formulaire web reste limité aux préréglages).
    return {
        "key": key or "generique",
        "label": channel or "Canal générique",
        "max_chars": None,
        "style": f"adapte le ton et le format aux usages habituels du canal « {channel} »",
    }


def _script_format_config(fmt: str) -> dict:
    key = (fmt or "").strip().lower()
    if key in SCRIPT_FORMATS:
        return {"key": key, "max_chars": None, **SCRIPT_FORMATS[key]}
    return {
        "key": key or "video_courte",
        "label": fmt or "Script vidéo courte",
        "duree": "30 secondes environ",
        "style": f"adapte le rythme et le ton au format « {fmt} »",
        "max_chars": None,
    }


def _asset_type_config(asset_type: str) -> Optional[dict]:
    key = (asset_type or "").strip().lower()
    if key not in ASSET_TYPES:
        return None
    return {"key": key, "max_chars": None, **ASSET_TYPES[key]}


# ---------------------------------------------------------------------------
# Base de données — table dédiée aux brouillons, créée/migrée à la demande
# (aucune dépendance à l'ordre de lancement bot.py / web.py).
# ---------------------------------------------------------------------------

def _ensure_table():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""CREATE TABLE IF NOT EXISTS drafts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        topic TEXT,
        channel TEXT NOT NULL,
        content TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'draft',
        source_ids TEXT,
        engine_used TEXT
    )""")
    existing_cols = {row[1] for row in conn.execute("PRAGMA table_info(drafts)")}
    if "content_type" not in existing_cols:
        conn.execute("ALTER TABLE drafts ADD COLUMN content_type TEXT NOT NULL DEFAULT 'post'")
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Rassemblement des sources réelles (anti-hallucination) via la recherche
# sémantique déjà en place pour l'agent de recherche.
# ---------------------------------------------------------------------------

def _gather_sources(topic: str, top_k: int = 15) -> list:
    return semantic_search.search(topic, top_k=top_k)


def _item_line(row: dict) -> str:
    label = row.get("title") or row.get("description") or "sans description"
    tags = row.get("tags")
    if isinstance(tags, str):
        try:
            tags = json.loads(tags)
        except (json.JSONDecodeError, TypeError):
            tags = []
    return f"#{row['id']} [{row.get('category') or '?'}] {label} (tags: {', '.join(tags or []) or '—'})"


# ---------------------------------------------------------------------------
# Prompts — un constructeur par type de contenu, tous ancrés dans les mêmes
# règles anti-hallucination (uniquement les éléments réels listés).
# ---------------------------------------------------------------------------

def _build_post_prompt(topic: str, cfg: dict, sources: list) -> str:
    lines = "\n".join(_item_line(r) for r in sources)
    limit_txt = (
        f"CONTRAINTE STRICTE : {cfg['max_chars']} caractères maximum (espaces compris)."
        if cfg["max_chars"] else
        "Pas de limite stricte de caractères, mais reste concis et adapté au canal."
    )
    return f"""Tu es le rédacteur de contenu de DiggApp, une application de curation personnelle \
(mode, sport, cuisine, déco, tech...).
{_editorial_block()}

À partir UNIQUEMENT des éléments réels ci-dessous, sauvegardés dans la collection, rédige un \
post prêt à publier sur : {cfg['label']}.

Style attendu pour ce canal : {cfg['style']}.
{limit_txt}

RÈGLES ABSOLUES :
1. Ne t'appuie QUE sur les éléments listés ci-dessous. N'invente aucun produit, marque, prix ou fait.
2. Si les éléments ne permettent pas de répondre au sujet demandé, indique-le dans "warning" plutôt \
que d'inventer du contenu.
3. Le texte du post doit respecter la contrainte de longueur ci-dessus.
4. Liste dans "source_ids" les identifiants (#id) des éléments effectivement utilisés.
5. Reconnais les synonymes et équivalents français/anglais du domaine avant de juger la \
pertinence d'un élément (ex. "manches longues" = "longsleeve"/"long sleeve", "baskets" = \
"sneakers", "sweat à capuche" = "hoodie") : un élément qui utilise le terme anglais n'est \
pas hors sujet pour une demande formulée en français.

Sujet demandé : "{topic}"

Éléments disponibles dans la collection :
{lines}

Réponds avec UNIQUEMENT un objet JSON valide (pas de markdown, pas d'explication) au format :
{{"post": "<texte du post>", "source_ids": [<id1>, <id2>], "warning": <null ou message si les données sont insuffisantes>}}"""


def _build_script_prompt(topic: str, cfg: dict, sources: list) -> str:
    lines = "\n".join(_item_line(r) for r in sources)
    return f"""Tu es scénariste de contenu pour DiggApp, une application de curation personnelle \
(mode, sport, cuisine, déco, tech...).
{_editorial_block()}

À partir UNIQUEMENT des éléments réels ci-dessous, sauvegardés dans la collection, construis un \
script de tournage pour une vidéo au format : {cfg['label']} (durée indicative : {cfg['duree']}).

Style attendu : {cfg['style']}.
Pense accessibilité : les textes à l'écran doivent être clairs et lisibles — ils serviront aussi de \
base à des sous-titres ajoutés au montage.

RÈGLES ABSOLUES :
1. Ne t'appuie QUE sur les éléments listés ci-dessous. N'invente aucun produit, marque, prix ou fait.
2. Si les éléments ne permettent pas de construire un script pertinent, indique-le dans "warning".
3. Structure le script en 3 à 6 plans maximum, chacun avec une description de l'image/action, un \
texte à l'écran, et éventuellement une voix off.
4. Liste dans "source_ids" les identifiants (#id) des éléments effectivement utilisés.
5. Reconnais les synonymes et équivalents français/anglais du domaine avant de juger la \
pertinence d'un élément (ex. "manches longues" = "longsleeve"/"long sleeve", "baskets" = \
"sneakers", "sweat à capuche" = "hoodie") : un élément qui utilise le terme anglais n'est \
pas hors sujet pour une demande formulée en français.

Sujet demandé : "{topic}"

Éléments disponibles dans la collection :
{lines}

Réponds avec UNIQUEMENT un objet JSON valide (pas de markdown, pas d'explication) au format :
{{"hook": "<accroche des 2 premières secondes>", "shots": [{{"description": "<image/action>", \
"duree_s": <nombre>, "texte_ecran": "<texte affiché>", "voix_off": "<texte dit, ou vide>"}}], \
"cta": "<appel à l'action final>", "source_ids": [<id1>, <id2>], "warning": <null ou message>}}"""


def _build_ai_prompt_prompt(topic: str, cfg: dict, sources: list) -> str:
    lines = "\n".join(_item_line(r) for r in sources)
    return f"""Tu es directeur artistique pour DiggApp, une application de curation personnelle \
(mode, sport, cuisine, déco, tech...).
{_editorial_block()}

À partir UNIQUEMENT des éléments réels ci-dessous, rédige un prompt prêt à être copié-collé dans un \
outil de génération IA pour produire : {cfg['label']}.

Consignes de rédaction du prompt : {cfg['guidance']}.
Angle éco-conception : privilégie une direction artistique sobre et réutilisable, sans surenchère \
d'éléments superflus dans le rendu demandé.

RÈGLES ABSOLUES :
1. Ne t'appuie QUE sur les éléments listés ci-dessous pour définir le sujet et le style. N'invente \
aucun produit, marque ou fait qui ne s'y trouve pas.
2. Si les éléments ne permettent pas de construire un prompt pertinent, indique-le dans "warning".
3. Liste dans "source_ids" les identifiants (#id) des éléments effectivement utilisés pour inspirer le prompt.
4. Reconnais les synonymes et équivalents français/anglais du domaine avant de juger la \
pertinence d'un élément (ex. "manches longues" = "longsleeve"/"long sleeve", "baskets" = \
"sneakers", "sweat à capuche" = "hoodie") : un élément qui utilise le terme anglais n'est \
pas hors sujet pour une demande formulée en français.

Sujet demandé : "{topic}"

Éléments disponibles dans la collection :
{lines}

Réponds avec UNIQUEMENT un objet JSON valide (pas de markdown, pas d'explication) au format :
{{"prompt": "<prompt prêt à coller>", "outil_suggere": "<ex. Kling, Higgsfield, Grok Imagine, Midjourney>", \
"source_ids": [<id1>, <id2>], "warning": <null ou message>}}"""


def _format_script(data: dict) -> str:
    lines = [f"🎬 ACCROCHE (0-2s) : {data.get('hook', '')}", ""]
    for i, shot in enumerate(data.get("shots") or [], 1):
        duree = shot.get("duree_s")
        duree_txt = f"{duree}s" if duree else "?"
        lines.append(f"Plan {i} ({duree_txt}) : {shot.get('description', '')}")
        if shot.get("texte_ecran"):
            lines.append(f"   Texte à l'écran : {shot['texte_ecran']}")
        if shot.get("voix_off"):
            lines.append(f"   Voix off : {shot['voix_off']}")
        lines.append("")
    if data.get("cta"):
        lines.append(f"CTA final : {data['cta']}")
        lines.append("")
    lines.append("⚠️ Penser à ajouter des sous-titres au montage (accessibilité).")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Génération — cloud (Gemini) en priorité, repli local (llama3.2)
# ---------------------------------------------------------------------------

def _call_gemini(prompt: str) -> Optional[str]:
    if not ai_engine.GEMINI_API_KEY:
        return None
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{ai_engine.GEMINI_MODEL}:generateContent"
    resp = requests.post(
        url,
        headers={"x-goog-api-key": ai_engine.GEMINI_API_KEY},
        json={
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": ai_engine.GEMINI_GENERATION_CONFIG,
        },
        timeout=ai_engine.AI_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()["candidates"][0]["content"]["parts"][0]["text"]


def _call_local(prompt: str) -> Optional[str]:
    resp = requests.post(
        f"{ai_engine.OLLAMA_BASE}/api/generate",
        json={"model": LOCAL_CONTENT_MODEL, "prompt": prompt, "stream": False, "format": "json"},
        timeout=ai_engine.AI_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()["response"]


def _generate_raw(prompt: str) -> tuple:
    """Renvoie (texte_brut, moteur_utilisé) ou (None, None) si les deux moteurs échouent."""
    try:
        raw = _call_gemini(prompt)
        if raw:
            logger.info("Studio : contenu généré par le moteur cloud")
            return raw, "cloud"
    except Exception as e:
        logger.warning("Génération cloud indisponible (%s)", e)

    try:
        raw = _call_local(prompt)
        if raw:
            logger.info("Studio : contenu généré par le moteur local")
            return raw, "local"
    except Exception as e:
        logger.error("Génération locale indisponible (%s)", e)

    return None, None


def _enforce_limit(text: str, max_chars: Optional[int]) -> tuple:
    """Ne fait jamais confiance à l'IA pour compter les caractères : on vérifie
    et on tronque proprement si besoin. Renvoie (texte, a_été_tronqué)."""
    if not max_chars or len(text) <= max_chars:
        return text, False
    cut = text[:max_chars - 1].rsplit(" ", 1)[0]
    return cut + "…", True


# ---------------------------------------------------------------------------
# Point d'entrée
# ---------------------------------------------------------------------------

def generate_draft(topic: str, target: str, content_type: str = "post") -> dict:
    """Génère un brouillon (post, script ou prompt IA), le sauvegarde en base et
    le renvoie. Ne lève jamais d'exception : renvoie {"error": "..."} en cas d'échec.
    """
    _ensure_table()
    topic = (topic or "").strip()
    if not topic:
        return {"error": "Précise un sujet pour générer un contenu (ex. « manches longues été »)."}

    content_type = (content_type or "post").strip().lower()
    if content_type not in CONTENT_TYPES:
        return {"error": f"Type de contenu inconnu : {content_type}"}

    sources = _gather_sources(topic)
    if not sources:
        return {"error": "Rien dans ta collection ne correspond à ce sujet — impossible de "
                          "générer un contenu fiable sans données réelles derrière."}

    if content_type == "post":
        cfg = _channel_config(target)
        prompt = _build_post_prompt(topic, cfg, sources)
    elif content_type == "script":
        cfg = _script_format_config(target)
        prompt = _build_script_prompt(topic, cfg, sources)
    else:  # ai_prompt
        cfg = _asset_type_config(target)
        if cfg is None:
            return {"error": "Précise le type d'asset pour le prompt IA : « image » ou « video »."}
        prompt = _build_ai_prompt_prompt(topic, cfg, sources)

    raw, engine_used = _generate_raw(prompt)
    if not raw:
        return {"error": "L'IA (cloud et local) est indisponible pour le moment, réessaie plus tard."}

    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not match:
        logger.error("Pas de JSON dans la réponse IA : %s", raw[:300])
        return {"error": "Réponse IA invalide, réessaie."}
    try:
        data = json.loads(match.group())
    except json.JSONDecodeError as e:
        logger.error("JSON invalide (%s) : %s", e, raw[:300])
        return {"error": "Réponse IA invalide, réessaie."}

    warning = data.get("warning") or None
    source_ids = [int(i) for i in (data.get("source_ids") or []) if str(i).lstrip("-").isdigit()]

    if content_type == "post":
        text = str(data.get("post") or "").strip()
        if not text:
            return {"error": warning or "L'IA n'a pas pu générer de post pour ce sujet."}
        text, truncated = _enforce_limit(text, cfg["max_chars"])
    elif content_type == "script":
        if not data.get("hook") and not data.get("shots"):
            return {"error": warning or "L'IA n'a pas pu générer de script pour ce sujet."}
        text = _format_script(data)
        truncated = False
    else:
        text = str(data.get("prompt") or "").strip()
        if not text:
            return {"error": warning or "L'IA n'a pas pu générer de prompt pour ce sujet."}
        outil = data.get("outil_suggere")
        if outil:
            text = f"{text}\n\n(Outil suggéré : {outil})"
        truncated = False

    now = datetime.now(timezone.utc).isoformat()
    conn = sqlite3.connect(DB_PATH)
    cur = conn.execute(
        """INSERT INTO drafts (created_at, updated_at, topic, channel, content, status, source_ids, engine_used, content_type)
           VALUES (?, ?, ?, ?, ?, 'draft', ?, ?, ?)""",
        (now, now, topic, cfg["key"], text, json.dumps(source_ids), engine_used, content_type),
    )
    draft_id = cur.lastrowid
    conn.commit()
    conn.close()

    return {
        "id": draft_id,
        "topic": topic,
        "content_type": content_type,
        "channel": cfg["key"],
        "channel_label": cfg["label"],
        "content": text,
        "status": "draft",
        "source_ids": source_ids,
        "engine_used": engine_used,
        "truncated": truncated,
        "char_count": len(text),
        "max_chars": cfg.get("max_chars"),
        "warning": warning,
    }


def _cfg_for(content_type: str, channel: str) -> Optional[dict]:
    if content_type == "post":
        return CHANNELS.get(channel)
    if content_type == "script":
        return SCRIPT_FORMATS.get(channel)
    if content_type == "ai_prompt":
        return ASSET_TYPES.get(channel)
    return None


def list_drafts(status: Optional[str] = None) -> list:
    _ensure_table()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    sql = "SELECT * FROM drafts"
    params = []
    if status:
        sql += " WHERE status = ?"
        params.append(status)
    sql += " ORDER BY created_at DESC"
    rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
    conn.close()

    for r in rows:
        try:
            r["source_ids"] = json.loads(r["source_ids"]) if r["source_ids"] else []
        except (json.JSONDecodeError, TypeError):
            r["source_ids"] = []
        ctype = r.get("content_type") or "post"
        cfg = _cfg_for(ctype, r["channel"])
        r["channel_label"] = cfg["label"] if cfg else r["channel"]
        r["max_chars"] = cfg.get("max_chars") if cfg else None
    return rows


def update_draft(draft_id: int, content: Optional[str] = None, status: Optional[str] = None) -> bool:
    _ensure_table()
    fields, params = [], []
    if content is not None:
        fields.append("content = ?")
        params.append(content)
    if status is not None:
        if status not in ("draft", "published", "discarded"):
            return False
        fields.append("status = ?")
        params.append(status)
    if not fields:
        return False
    fields.append("updated_at = ?")
    params.append(datetime.now(timezone.utc).isoformat())
    params.append(draft_id)

    conn = sqlite3.connect(DB_PATH)
    conn.execute(f"UPDATE drafts SET {', '.join(fields)} WHERE id = ?", params)
    changed = conn.total_changes > 0
    conn.commit()
    conn.close()
    return changed


def delete_draft(draft_id: int) -> bool:
    _ensure_table()
    conn = sqlite3.connect(DB_PATH)
    conn.execute("DELETE FROM drafts WHERE id = ?", (draft_id,))
    changed = conn.total_changes > 0
    conn.commit()
    conn.close()
    return changed
