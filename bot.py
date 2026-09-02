import os
import uuid
import sqlite3
import logging
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

import ai_engine
import link_extractor
import search_engine

load_dotenv()

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
DB_PATH = Path("hub.db")
CAPTURES_DIR = Path("data/captures")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Base de données
# ---------------------------------------------------------------------------

def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS captures (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at      TEXT NOT NULL,
            source          TEXT NOT NULL,
            media_type      TEXT NOT NULL,
            file_path       TEXT NOT NULL,
            telegram_file_id TEXT NOT NULL,
            description     TEXT,
            category        TEXT,
            tags            TEXT
        )
    """)

    # Migration : colonnes ajoutées pour l'ingestion de liens (ré-exécutable sans erreur)
    existing = {row[1] for row in conn.execute("PRAGMA table_info(captures)")}
    new_columns = {
        "item_type": "TEXT",
        "url": "TEXT",
        "platform": "TEXT",
        "title": "TEXT",
        "thumbnail_path": "TEXT",
        "user_note": "TEXT",
    }
    for col, col_type in new_columns.items():
        if col not in existing:
            conn.execute(f"ALTER TABLE captures ADD COLUMN {col} {col_type}")
            logger.info("Migration : colonne %s ajoutée", col)

    conn.execute("UPDATE captures SET item_type = 'image' WHERE item_type IS NULL")
    conn.commit()
    conn.close()


def save_to_db(item_type: str, file_path: str, file_id: str, metadata: Optional[dict],
               url: Optional[str] = None, platform: Optional[str] = None,
               title: Optional[str] = None, thumbnail_path: Optional[str] = None,
               user_note: Optional[str] = None) -> int:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.execute(
        """INSERT INTO captures
           (created_at, source, media_type, file_path, telegram_file_id,
            description, category, tags, item_type, url, platform, title, thumbnail_path, user_note)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            datetime.now(timezone.utc).isoformat(),
            "telegram",
            item_type,
            file_path,
            file_id,
            metadata.get("description") if metadata else None,
            metadata.get("category") if metadata else None,
            json.dumps(metadata.get("tags", []), ensure_ascii=False) if metadata else None,
            item_type,
            url,
            platform,
            title,
            thumbnail_path,
            user_note,
        ),
    )
    row_id = cur.lastrowid
    conn.commit()
    conn.close()
    return row_id


# ---------------------------------------------------------------------------
# Recherche (logique partagée avec le web dans search_engine.py)
# ---------------------------------------------------------------------------

async def run_search(update: Update, query: str):
    if search_engine.db_is_empty():
        await update.message.reply_text(
            "Ta collection est vide pour l'instant. Envoie-moi des photos ou des liens pour commencer !"
        )
        return

    await update.message.reply_text("🔍 Recherche en cours…")

    intent = ai_engine.extract_search_intent(query)
    logger.info("Intention extraite : %s", intent)
    results = search_engine.search_db(query, intent)
    if not results:
        await update.message.reply_text(
            "Aucun élément trouvé pour cette recherche, essaie avec d'autres mots-clés."
        )
        return

    await update.message.reply_text(search_engine.summarize_results(query, results))

    # Envoyer les 3 premiers résultats : image ou lien
    for row in results[:3]:
        if row.get("item_type") == "link":
            text = f"🔗 {row.get('title') or row['url']}\n{row['url']}"
            await update.message.reply_text(text)
            continue
        image_path = Path(row["file_path"])
        if image_path.exists():
            try:
                with open(image_path, "rb") as f:
                    caption = row.get("description") or image_path.name
                    await update.message.reply_photo(photo=f, caption=caption[:1024])
            except Exception as e:
                logger.error("Impossible d'envoyer l'image %s : %s", image_path, e)
        else:
            logger.warning("Fichier introuvable : %s", image_path)


# ---------------------------------------------------------------------------
# Ingestion de liens
# ---------------------------------------------------------------------------

async def ingest_link(update: Update, url: str, note: Optional[str] = None):
    await update.message.reply_text("🔗 Lien reçu, extraction en cours…")

    meta = link_extractor.extract(url)
    platform = meta["platform"]
    extraction_failed = meta["method"] == "none"

    if extraction_failed and not note and not meta.get("thumbnail_path"):
        # Rien d'exploitable : on stocke le minimum et on demande de l'aide
        save_to_db("link", "", "", None, url=url, platform=platform, title=meta["title"])
        await update.message.reply_text(
            f"⚠️ Extraction limitée pour ce lien {platform} (la plateforme bloque l'accès).\n"
            f"Je l'ai sauvegardé, mais renvoie-le avec une note pour m'aider à le classer "
            f"(ex : « {url} tuto colorimétrie DaVinci ») — ou envoie une capture d'écran à la place."
        )
        return

    text_parts = [f"Platform: {platform}"]
    if not extraction_failed:
        text_parts.append(f"Title: {meta['title']}")
        if meta.get("uploader"):
            text_parts.append(f"Author: {meta['uploader']}")
        if meta.get("description"):
            text_parts.append(f"Description: {meta['description']}")
    content = "\n".join(text_parts)

    thumbnail = Path(meta["thumbnail_path"]) if meta.get("thumbnail_path") else None
    analysis = ai_engine.analyze(content, "text", image_path=thumbnail, user_note=note)

    # Si l'extraction a échoué, le titre plateforme générique ne vaut rien :
    # la note de l'utilisateur fait un meilleur titre
    title = meta["title"] if not extraction_failed else (note or meta["title"])

    save_to_db(
        "link", meta.get("thumbnail_path") or "", "", analysis,
        url=url, platform=platform, title=title,
        thumbnail_path=meta.get("thumbnail_path"), user_note=note,
    )

    if analysis:
        tags_str = " ".join(f"#{t}" for t in analysis.get("tags", []))
        engine_label = "🖥 local" if analysis["engine_used"] == "local" else "☁️ cloud"
        reply = (
            f"✅ *{title[:100]}*\n\n"
            f"🌐 Plateforme : `{platform}`\n"
            f"📂 Catégorie : `{analysis['category']}`\n"
            f"📝 {analysis.get('description', '—')}\n"
            f"🏷 {tags_str}\n"
            f"🤖 Moteur : {engine_label}"
        )
        if extraction_failed:
            reply += "\n\n⚠️ Extraction limitée : classement basé sur ta note et/ou la miniature."
    else:
        reply = (
            f"✅ Lien sauvegardé : {title[:100]}\n"
            "⚠️ Analyse IA indisponible (Ollama et Gemini hors ligne ?). Le lien est bien enregistré."
        )

    await update.message.reply_text(reply, parse_mode="Markdown")


# ---------------------------------------------------------------------------
# Handlers Telegram
# ---------------------------------------------------------------------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Bienvenue sur ton Hub IA !\n\n"
        "📸 Envoie-moi une photo : je l'analyse et la range automatiquement.\n"
        "🔗 Envoie-moi un lien (TikTok, Instagram, Pinterest, YouTube, X, site web) : "
        "je récupère les infos et je le classe.\n"
        "💬 Envoie-moi du texte : je cherche dans ta collection (images ET liens).\n\n"
        "Commandes :\n"
        "/moteur — voir ou changer le moteur IA (local/cloud/auto)\n"
        "/stats — statistiques de ta collection"
    )


async def moteur(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.args:
        mode = context.args[0].lower()
        if ai_engine.set_engine_mode(mode):
            await update.message.reply_text(f"✅ Moteur IA basculé sur : `{mode}`", parse_mode="Markdown")
        else:
            await update.message.reply_text("❌ Mode invalide. Choisis : local, cloud ou auto.")
        return

    mode = ai_engine.get_engine_mode()
    await update.message.reply_text(
        f"🤖 Moteur actif : `{mode}`\n\n"
        "• `local` — qwen2.5vl:7b via Ollama\n"
        "• `cloud` — Gemini 2.0 Flash\n"
        "• `auto` — local d'abord, cloud en secours\n\n"
        "Pour changer : /moteur local | cloud | auto",
        parse_mode="Markdown",
    )


async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if search_engine.db_is_empty():
        await update.message.reply_text("Ta collection est vide pour l'instant.")
        return

    s = search_engine.db_stats()
    type_labels = {"image": "📸 Images", "link": "🔗 Liens"}
    lines = ["📊 *Ta collection*\n"]
    for item_type, count in s["by_type"]:
        lines.append(f"{type_labels.get(item_type, item_type)} : {count}")
    lines.append("\n*Par catégorie :*")
    for category, count in s["by_category"]:
        lines.append(f"• {category} : {count}")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if not text:
        return

    url, note = link_extractor.split_url_and_note(text)
    try:
        if url:
            logger.info("Ingestion de lien : %s (note : %s)", url, note)
            await ingest_link(update, url, note)
        else:
            logger.info("Recherche : %s", text)
            await run_search(update, text)
    except Exception as e:
        logger.error("Erreur handle_text : %s", e, exc_info=True)
        await update.message.reply_text("⚠️ Une erreur est survenue, mais le bot tourne toujours. Réessaie !")


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        photo = update.message.photo[-1]
        file = await context.bot.get_file(photo.file_id)

        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        unique_name = f"{timestamp}_{uuid.uuid4().hex[:8]}.jpg"
        file_path = CAPTURES_DIR / unique_name

        await file.download_to_drive(file_path)
        logger.info("Image sauvegardée : %s", file_path)

        await update.message.reply_text("📥 Image reçue, analyse en cours…")

        note = (update.message.caption or "").strip() or None
        metadata = ai_engine.analyze(file_path, "image", user_note=note)
        save_to_db("image", str(file_path), photo.file_id, metadata, user_note=note)

        if metadata:
            tags_str = " ".join(f"#{t}" for t in metadata.get("tags", []))
            engine_label = "🖥 local" if metadata["engine_used"] == "local" else "☁️ cloud"
            reply = (
                f"✅ *{unique_name}*\n\n"
                f"📂 Catégorie : `{metadata['category']}`\n"
                f"📝 {metadata.get('description', '—')}\n"
                f"🏷 {tags_str}\n"
                f"🤖 Moteur : {engine_label}"
            )
        else:
            reply = (
                f"✅ Image sauvegardée : `{unique_name}`\n"
                "⚠️ Analyse IA indisponible (Ollama et Gemini hors ligne ?). L'image est bien enregistrée."
            )

        await update.message.reply_text(reply, parse_mode="Markdown")
    except Exception as e:
        logger.error("Erreur handle_photo : %s", e, exc_info=True)
        await update.message.reply_text("⚠️ Une erreur est survenue avec cette image. Réessaie !")


def main():
    if not TOKEN:
        raise ValueError("TELEGRAM_BOT_TOKEN manquant dans le fichier .env")

    CAPTURES_DIR.mkdir(parents=True, exist_ok=True)
    link_extractor.THUMBNAILS_DIR.mkdir(parents=True, exist_ok=True)
    init_db()

    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("moteur", moteur))
    app.add_handler(CommandHandler("stats", stats))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    logger.info("Bot démarré (moteur IA : %s) — en attente de messages…", ai_engine.get_engine_mode())
    app.run_polling()


if __name__ == "__main__":
    main()
