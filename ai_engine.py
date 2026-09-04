"""Moteur IA unifié : local (Ollama qwen2.5vl) ou cloud (Google Gemini).

Point d'entrée unique : analyze(content, content_type, image_path=None)
qui renvoie toujours le même format JSON :
{"category": "...", "tags": [...], "description": "...", "engine_used": "local|cloud"}
"""

import os
import re
import json
import time
import base64
import logging
from pathlib import Path
from typing import Optional

import requests
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

OLLAMA_BASE = os.getenv("OLLAMA_BASE", "http://localhost:11434")
LOCAL_MODEL = "qwen2.5vl:7b"
# gemini-2.0-flash a été retiré par Google ; flash-lite-latest = le flash le moins
# cher, et thinkingLevel "minimal" est indispensable pour répondre en <10s au lieu de 60s+
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-flash-lite-latest")
GEMINI_GENERATION_CONFIG = {"thinkingConfig": {"thinkingLevel": "minimal"}}
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

AI_TIMEOUT = 60  # secondes, pour chaque appel IA
GEMINI_RETRY_DELAY = 1.5  # secondes avant un unique retry sur panne temporaire Gemini

VALID_CATEGORIES = [
    "mode", "sport", "cuisine", "restaurant", "deco", "tech",
    "audiovisuel", "tuto", "film_serie", "inspiration", "autre",
]

VALID_MODES = ("local", "cloud", "auto")

_engine_mode = os.getenv("AI_ENGINE", "auto").strip().lower()
if _engine_mode not in VALID_MODES:
    _engine_mode = "auto"


def get_engine_mode() -> str:
    return _engine_mode


def set_engine_mode(mode: str) -> bool:
    """Change le mode à chaud (local/cloud/auto). Retourne False si le mode est invalide."""
    global _engine_mode
    mode = mode.strip().lower()
    if mode not in VALID_MODES:
        return False
    _engine_mode = mode
    logger.info("Mode moteur IA changé : %s", mode)
    return True


def _is_transient_gemini_error(exc: Exception) -> bool:
    """Panne temporaire côté Google : timeout réseau ou 503 (modèle surchargé).
    Ce sont les seuls cas qui méritent un retry — une clé invalide (401/403) ou
    une requête malformée (400) échoueront pareil au 2e essai."""
    if isinstance(exc, (requests.Timeout, requests.ConnectionError)):
        return True
    resp = getattr(exc, "response", None)
    return resp is not None and resp.status_code == 503


def post_gemini(payload: dict) -> requests.Response:
    """POST vers generateContent avec UN retry sur panne temporaire (503/timeout).

    Partagé par tous les appels Gemini (analyse, intention, génération, agent).
    Sur panne temporaire : attend GEMINI_RETRY_DELAY puis retente une fois. Si le
    2e essai échoue aussi (ou si l'erreur n'est pas temporaire), lève l'exception —
    l'appelant bascule alors sur le local. Les deux cas sont logués distinctement.
    """
    if not GEMINI_API_KEY:
        raise ValueError("GEMINI_API_KEY manquante dans .env")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"
    headers = {"x-goog-api-key": GEMINI_API_KEY}
    for attempt in (1, 2):
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=AI_TIMEOUT)
            resp.raise_for_status()
            return resp
        except Exception as e:
            if attempt == 1 and _is_transient_gemini_error(e):
                logger.warning("Gemini : panne temporaire (%s) — RETRY dans %.1fs", e, GEMINI_RETRY_DELAY)
                time.sleep(GEMINI_RETRY_DELAY)
                continue
            raise


CATEGORY_REFERENTIAL = """CATEGORIES (pick exactly ONE, the DOMINANT one):
- mode: clothing, shoes, sneakers, accessories, watches, outfits, fashion brands
- sport: sports equipment, workouts, gym, sports nutrition, athletic performance. WARNING: sneakers or sandals are "mode", not "sport", unless the context is explicitly training/working out
- cuisine: recipes, cooking techniques, ingredients, kitchen utensils
- restaurant: addresses, places to eat, bars, cafés
- deco: furniture, interior decoration, home layout, decorative objects, home inspiration
- tech: computer hardware, software, gadgets, AI, programming
- audiovisuel: filming gear, cameras, lenses, lighting, audio, video editing, color grading
- tuto: generic "how to" educational content. IMPORTANT: if a tutorial is about a specific domain, use that domain's category instead (a DaVinci Resolve tutorial = audiovisuel, not tuto). Use "tuto" only when the topic fits no other category
- film_serie: movies, series, documentaries, watch recommendations
- inspiration: moodboards, visual references, art, inspirational photography
- autre: only if nothing else fits"""


def _build_prompt(text: str, has_image: bool, user_note: Optional[str] = None) -> str:
    if has_image and not text:
        subject = "the image"
    elif has_image:
        subject = "the image and the accompanying text"
    else:
        subject = "the text"
    prompt = f"""You are a content classifier for a personal collection. Analyze {subject} and extract structured metadata.

{CATEGORY_REFERENTIAL}

RULES:
- Exactly ONE category: the DOMINANT topic of the content
- tags must be consistent with the chosen category (if the tags are about fashion, the category cannot be sport)
- tags: 3 to 6, in French, lowercase, SPECIFIC (brand, model, material, style, dish name). NEVER generic words like "photo", "image", "vidéo", "contenu", and NEVER platform names like "tiktok", "instagram", "youtube", "pinterest"
- description: one factual French sentence describing the CONTENT itself (product, subject, brand, price), not the medium or the platform
- description MUST include the USE or usage context of the object when it can be deduced (from the model, brand, design or context), not only a neutral visual description. Example: for Asics Gel-Kayano sneakers, write "Chaussures de running Asics Gel-Kayano, modèle de course à pied", NOT just "Paire de baskets Asics Gel-Kayano". Same idea for e.g. "veste de sport d'entraînement", "fauteuil de salon scandinave"
- if a price or a product reference is visible, include it in the description

Respond with ONLY a valid JSON object (no markdown, no explanation) in this exact format:
{{"category": "<category>", "tags": ["tag1", "tag2", "tag3"], "description": "<one factual French sentence>"}}"""
    if user_note:
        prompt += f"""

USER NOTE (AUTHORITATIVE): the user added this note about the content: "{user_note}"
This note takes priority over any extracted metadata. If it explicitly states or implies a category, you MUST use that category."""
    if text:
        prompt += f"\n\nContent to analyze:\n{text}"
    return prompt


def _parse_metadata(raw: str) -> dict:
    """Extrait et valide le JSON renvoyé par le modèle."""
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not match:
        raise ValueError(f"Pas de JSON trouvé dans la réponse : {raw[:200]}")
    data = json.loads(match.group())

    if data.get("category") not in VALID_CATEGORIES:
        data["category"] = "autre"
    if isinstance(data.get("tags"), list):
        data["tags"] = [str(t).lower() for t in data["tags"]][:5]
    else:
        data["tags"] = []
    data["description"] = str(data.get("description") or "").strip()
    return {"category": data["category"], "tags": data["tags"], "description": data["description"]}


def _analyze_local(prompt: str, image_b64: Optional[str]) -> dict:
    payload = {
        "model": LOCAL_MODEL,
        "prompt": prompt,
        "stream": False,
        "format": "json",
    }
    if image_b64:
        payload["images"] = [image_b64]
    resp = requests.post(f"{OLLAMA_BASE}/api/generate", json=payload, timeout=AI_TIMEOUT)
    resp.raise_for_status()
    return _parse_metadata(resp.json()["response"])


def _analyze_cloud(prompt: str, image_b64: Optional[str]) -> dict:
    parts = [{"text": prompt}]
    if image_b64:
        parts.append({"inline_data": {"mime_type": "image/jpeg", "data": image_b64}})
    resp = post_gemini({"contents": [{"parts": parts}], "generationConfig": GEMINI_GENERATION_CONFIG})
    raw = resp.json()["candidates"][0]["content"]["parts"][0]["text"]
    return _parse_metadata(raw)


def analyze(content, content_type: str = "text", image_path: Optional[Path] = None,
            user_note: Optional[str] = None) -> Optional[dict]:
    """Analyse un contenu et renvoie les métadonnées unifiées, ou None si tout échoue.

    - content_type="image" : content est le chemin de l'image
    - content_type="text"  : content est le texte ; image_path optionnel (miniature)
    - user_note : consigne de l'utilisateur, prioritaire sur les métadonnées extraites
    """
    if content_type == "image":
        image_path = Path(content)
        text = ""
    else:
        text = str(content or "")

    image_b64 = None
    if image_path:
        try:
            image_b64 = base64.b64encode(Path(image_path).read_bytes()).decode("utf-8")
        except OSError as e:
            logger.warning("Impossible de lire l'image %s : %s", image_path, e)

    prompt = _build_prompt(text, has_image=bool(image_b64), user_note=user_note)
    mode = _engine_mode

    local_result = None
    if mode in ("local", "auto"):
        try:
            local_result = _analyze_local(prompt, image_b64)
            local_result["engine_used"] = "local"
        except Exception as e:
            logger.warning("Moteur local indisponible (%s)", e)
            if mode == "local":
                return None

        if local_result is not None:
            # qwen2.5vl répond parfois "autre" à tort : en mode auto, on demande
            # un second avis au cloud avant d'accepter cette catégorie fourre-tout
            if mode == "auto" and local_result["category"] == "autre":
                logger.info("Catégorie 'autre' en local → second avis cloud")
            else:
                return local_result

    try:
        result = _analyze_cloud(prompt, image_b64)
        result["engine_used"] = "cloud"
        return result
    except Exception as e:
        logger.error("Gemini KO après retry → bascule locale définitive (%s)", e)
        # Le résultat local "autre" reste meilleur que rien si le cloud échoue
        return local_result


def extract_search_intent(query: str) -> Optional[dict]:
    """Extrait l'intention d'une requête de recherche. Retourne None si l'IA est indisponible."""
    categories_str = ", ".join(VALID_CATEGORIES)
    prompt = f"""A user searches their personal collection of saved images and links. Analyze their query and extract the search intent.

Respond with ONLY a valid JSON object (no markdown, no explanation) in this exact format:
{{"keywords": ["..."], "category": "<category or null>", "item_type": "<image, link or null>", "is_listing": true_or_false}}

- keywords: 1 to 5 French search keywords extracted from the query (lowercase, singular, no stopwords, no question words). Empty list if the user just wants to see everything.
- category: one of [{categories_str}] ONLY if the query clearly targets that category, else null
- item_type: "image" if the user asks about their images/photos/screenshots, "link" if about links/videos/urls, null otherwise
- is_listing: true ONLY if the user wants to list or count their content without a specific subject ("qu'est-ce que j'ai ?", "montre-moi tout", "combien de liens ?"). If the query mentions a specific subject, is_listing is false.

Examples:
- "J'ai quoi comme image ?" -> {{"keywords": [], "category": null, "item_type": "image", "is_listing": true}}
- "montre-moi mes recettes de pâtes" -> {{"keywords": ["recette", "pâtes"], "category": "cuisine", "item_type": null, "is_listing": false}}
- "les sneakers que j'ai sauvegardées" -> {{"keywords": ["sneakers"], "category": "mode", "item_type": null, "is_listing": false}}

Query: "{query}" """

    raw = None
    mode = _engine_mode

    if mode in ("local", "auto"):
        try:
            payload = {"model": LOCAL_MODEL, "prompt": prompt, "stream": False, "format": "json"}
            resp = requests.post(f"{OLLAMA_BASE}/api/generate", json=payload, timeout=AI_TIMEOUT)
            resp.raise_for_status()
            raw = resp.json()["response"]
        except Exception as e:
            logger.warning("Moteur local indisponible pour l'intention (%s)", e)
            if mode == "local":
                return None

    if raw is None:
        try:
            resp = post_gemini({"contents": [{"parts": [{"text": prompt}]}],
                                "generationConfig": GEMINI_GENERATION_CONFIG})
            raw = resp.json()["candidates"][0]["content"]["parts"][0]["text"]
        except Exception as e:
            logger.error("Gemini KO après retry pour l'intention → abandon (%s)", e)
            return None

    try:
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        data = json.loads(match.group()) if match else {}
    except (json.JSONDecodeError, AttributeError):
        return None

    keywords = data.get("keywords")
    intent = {
        "keywords": [str(k).lower() for k in keywords][:5] if isinstance(keywords, list) else [],
        "category": data.get("category") if data.get("category") in VALID_CATEGORIES else None,
        "item_type": data.get("item_type") if data.get("item_type") in ("image", "link") else None,
        "is_listing": bool(data.get("is_listing")),
    }
    return intent


def generate_text(prompt: str) -> Optional[str]:
    """Génération de texte libre (synthèse de recherche), avec la même bascule local/cloud."""
    mode = _engine_mode

    if mode in ("local", "auto"):
        try:
            payload = {"model": LOCAL_MODEL, "prompt": prompt, "stream": False}
            resp = requests.post(f"{OLLAMA_BASE}/api/generate", json=payload, timeout=AI_TIMEOUT)
            resp.raise_for_status()
            return resp.json()["response"].strip()
        except Exception as e:
            logger.warning("Moteur local indisponible pour la génération (%s)", e)
            if mode == "local":
                return None

    try:
        resp = post_gemini({"contents": [{"parts": [{"text": prompt}]}],
                            "generationConfig": GEMINI_GENERATION_CONFIG})
        return resp.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
    except Exception as e:
        logger.error("Gemini KO après retry pour la génération → abandon (%s)", e)
        return None
