"""Extraction de métadonnées depuis une URL : yt-dlp puis fallback Open Graph.

Point d'entrée : extract(url) qui renvoie toujours un dict :
{"url", "platform", "title", "description", "uploader", "thumbnail_path", "method"}
method vaut "yt-dlp", "opengraph" ou "none" (échec total, titre minimal).
"""

import re
import uuid
import logging
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

THUMBNAILS_DIR = Path("data/thumbnails")
EXTRACT_TIMEOUT = 30  # secondes

URL_PATTERN = re.compile(r"https?://[^\s<>\"]+")

BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

PLATFORMS = {
    "tiktok.com": "tiktok",
    "instagram.com": "instagram",
    "pinterest.": "pinterest",
    "pin.it": "pinterest",
    "youtube.com": "youtube",
    "youtu.be": "youtube",
    "twitter.com": "x",
    "x.com": "x",
}

# Liens courts à résoudre (redirection) avant extraction
SHORT_LINK_HOSTS = {"vm.tiktok.com", "vt.tiktok.com", "pin.it", "t.co", "bit.ly", "tinyurl.com"}

# Titres génériques renvoyés quand la plateforme bloque le scraping
GENERIC_TITLES = {
    "tiktok - make your day", "tiktok", "make your day", "watch on tiktok",
    "instagram", "login • instagram", "log in • instagram", "instagram photos and videos",
    "pinterest", "pinterest - france", "youtube", "x", "twitter",
    "x. it's what's happening", "log in", "connexion",
}


def find_url(text: str) -> Optional[str]:
    """Retourne la première URL trouvée dans le texte, ou None."""
    match = URL_PATTERN.search(text or "")
    return match.group().rstrip(".,)") if match else None


def split_url_and_note(text: str):
    """Sépare l'URL du reste du message (note utilisateur). Retourne (url, note|None)."""
    url = find_url(text)
    if not url:
        return None, None
    note = text.replace(url, " ").strip(" \n\t-–—:,")
    return url, (note if note else None)


def _resolve_short_url(url: str) -> str:
    """Suit les redirections des liens courts (vm.tiktok.com, pin.it…)."""
    host = (urlparse(url).netloc or "").lower().removeprefix("www.")
    if host not in SHORT_LINK_HOSTS:
        return url
    try:
        resp = requests.head(url, headers={"User-Agent": BROWSER_UA},
                             allow_redirects=True, timeout=10)
        if resp.url and resp.url != url:
            logger.info("Lien court résolu : %s → %s", url, resp.url)
            return resp.url
    except Exception as e:
        logger.info("Résolution du lien court impossible (%s), on garde l'original", e)
    return url


def _is_generic(meta: dict) -> bool:
    """Détecte des métadonnées inutilisables (page de login, titre de plateforme…)."""
    title = re.sub(r"\s+", " ", (meta.get("title") or "")).strip().lower()
    desc = (meta.get("description") or "").strip()
    if not title or title in GENERIC_TITLES:
        return True
    # Titre très court sans description : pas assez de matière pour classifier
    if len(title) + len(desc) < 25:
        return True
    return False


def detect_platform(url: str) -> str:
    host = (urlparse(url).netloc or "").lower().removeprefix("www.")
    for key, name in PLATFORMS.items():
        if key in host:
            return name
    return "web"


def _extract_ytdlp(url: str) -> Optional[dict]:
    import yt_dlp

    opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "socket_timeout": EXTRACT_TIMEOUT,
        "noplaylist": True,
        "http_headers": {"User-Agent": BROWSER_UA},
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
    if not info:
        return None
    return {
        "title": info.get("title"),
        "description": (info.get("description") or "")[:2000],
        "uploader": info.get("uploader") or info.get("channel"),
        "thumbnail_url": info.get("thumbnail"),
    }


def _extract_opengraph(url: str) -> Optional[dict]:
    headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}
    resp = requests.get(url, headers=headers, timeout=EXTRACT_TIMEOUT, allow_redirects=True)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    def og(prop):
        tag = soup.find("meta", property=f"og:{prop}") or soup.find("meta", attrs={"name": f"og:{prop}"})
        return tag.get("content", "").strip() if tag else None

    title = og("title") or (soup.title.string.strip() if soup.title and soup.title.string else None)
    if not title:
        return None
    return {
        "title": title,
        "description": (og("description") or "")[:2000],
        "uploader": og("site_name"),
        "thumbnail_url": og("image"),
    }


def _download_thumbnail(thumbnail_url: str) -> Optional[Path]:
    THUMBNAILS_DIR.mkdir(parents=True, exist_ok=True)
    resp = requests.get(thumbnail_url, timeout=EXTRACT_TIMEOUT)
    resp.raise_for_status()
    path = THUMBNAILS_DIR / f"{uuid.uuid4().hex[:12]}.jpg"
    path.write_bytes(resp.content)
    return path


def extract(url: str) -> dict:
    """Extrait les métadonnées d'une URL. Ne lève jamais d'exception.

    method == "none" signale un échec : métadonnées absentes OU génériques
    (page de login, "TikTok - Make Your Day"…). La miniature est conservée
    si une des tentatives en a trouvé une, même générique (utile à l'IA).
    """
    url = _resolve_short_url(url)
    platform = detect_platform(url)
    meta = None
    method = "none"
    thumbnail_url = None

    try:
        candidate = _extract_ytdlp(url)
        if candidate:
            thumbnail_url = candidate.get("thumbnail_url")
            if _is_generic(candidate):
                logger.info("yt-dlp n'a renvoyé que des métadonnées génériques pour %s", url)
            else:
                meta, method = candidate, "yt-dlp"
    except Exception as e:
        logger.info("yt-dlp a échoué pour %s : %s", url, e)

    if not meta:
        try:
            candidate = _extract_opengraph(url)
            if candidate:
                thumbnail_url = thumbnail_url or candidate.get("thumbnail_url")
                if _is_generic(candidate):
                    logger.info("Open Graph n'a renvoyé que des métadonnées génériques pour %s", url)
                else:
                    meta, method = candidate, "opengraph"
        except Exception as e:
            logger.info("Open Graph a échoué pour %s : %s", url, e)

    if not meta:
        host = urlparse(url).netloc or url
        meta = {"title": f"Lien {platform} ({host})", "description": "", "uploader": None}

    thumbnail_path = None
    if meta.get("thumbnail_url") or thumbnail_url:
        try:
            thumbnail_path = _download_thumbnail(meta.get("thumbnail_url") or thumbnail_url)
        except Exception as e:
            logger.info("Téléchargement de la miniature impossible : %s", e)

    return {
        "url": url,
        "platform": platform,
        "title": meta.get("title") or url,
        "description": meta.get("description") or "",
        "uploader": meta.get("uploader"),
        "thumbnail_path": str(thumbnail_path) if thumbnail_path else None,
        "method": method,
    }
