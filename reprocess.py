"""Relance l'analyse IA sur les éléments déjà en base avec les nouveaux prompts.

Usage :
    venv/bin/python reprocess.py                    # seulement les éléments sans catégorie
    venv/bin/python reprocess.py --all              # tout retraiter (analyse IA + embeddings)
    venv/bin/python reprocess.py --embeddings-only  # régénérer uniquement les embeddings
                                                    # depuis les métadonnées existantes (rapide)
"""

import argparse
import json
import sqlite3
from pathlib import Path

import ai_engine
import embeddings

DB_PATH = Path("hub.db")


def build_link_content(row) -> str:
    parts = [f"Platform: {row['platform'] or 'web'}"]
    if row["title"]:
        parts.append(f"Title: {row['title']}")
    if row["url"]:
        parts.append(f"URL: {row['url']}")
    return "\n".join(parts)


def reprocess_embeddings(conn):
    """Régénère les embeddings de tous les éléments à partir des métadonnées en base."""
    rows = conn.execute("SELECT * FROM captures ORDER BY id").fetchall()
    ok, failed = 0, 0
    for row in rows:
        note = row["user_note"] if "user_note" in row.keys() else None
        text = embeddings.build_item_text(row["category"], row["description"], row["tags"], note)
        embedding_json = embeddings.serialize(embeddings.get_embedding(text))
        if not embedding_json:
            print(f"✗ id {row['id']} : embedding indisponible (texte vide ou Ollama hors ligne)")
            failed += 1
            continue
        conn.execute("UPDATE captures SET embedding = ? WHERE id = ?", (embedding_json, row["id"]))
        conn.commit()
        ok += 1
        print(f"✓ id {row['id']} : embedding régénéré ({text[:60]})")
    print(f"\nTerminé : {ok} embedding(s), {failed} échec(s).")


def main():
    parser = argparse.ArgumentParser(description="Retraite les analyses IA de hub.db")
    parser.add_argument("--all", action="store_true", help="retraiter tous les éléments, pas seulement ceux sans catégorie")
    parser.add_argument("--embeddings-only", action="store_true",
                        help="régénérer uniquement les embeddings depuis les métadonnées existantes, sans analyse IA")
    args = parser.parse_args()

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    if args.embeddings_only:
        reprocess_embeddings(conn)
        conn.close()
        return

    where = "" if args.all else "WHERE category IS NULL OR category = ''"
    rows = conn.execute(f"SELECT * FROM captures {where} ORDER BY id").fetchall()

    if not rows:
        print("Rien à retraiter.")
        return

    print(f"{len(rows)} élément(s) à retraiter (moteur : {ai_engine.get_engine_mode()})\n")
    ok, failed = 0, 0

    for row in rows:
        item_type = row["item_type"] or "image"
        note = row["user_note"] if "user_note" in row.keys() else None
        label = row["title"] or row["file_path"] or row["url"] or f"id {row['id']}"

        if item_type == "image":
            image = Path(row["file_path"]) if row["file_path"] else None
            if not image or not image.exists():
                print(f"✗ id {row['id']} : fichier introuvable ({row['file_path']})")
                failed += 1
                continue
            analysis = ai_engine.analyze(image, "image", user_note=note)
        else:
            thumb = Path(row["thumbnail_path"]) if row["thumbnail_path"] else None
            if thumb and not thumb.exists():
                thumb = None
            analysis = ai_engine.analyze(build_link_content(row), "text", image_path=thumb, user_note=note)

        if not analysis:
            print(f"✗ id {row['id']} : analyse IA indisponible ({label[:60]})")
            failed += 1
            continue

        embedding_json = embeddings.serialize(embeddings.get_embedding(
            embeddings.build_item_text(
                analysis["category"], analysis["description"], analysis["tags"], note
            )
        ))

        conn.execute(
            "UPDATE captures SET category = ?, tags = ?, description = ?, embedding = ? WHERE id = ?",
            (
                analysis["category"],
                json.dumps(analysis["tags"], ensure_ascii=False),
                analysis["description"],
                embedding_json,
                row["id"],
            ),
        )
        conn.commit()
        ok += 1
        embed_flag = "🧭" if embedding_json else "∅ embedding"
        print(f"✓ id {row['id']} [{analysis['engine_used']}] {item_type} → {analysis['category']} | {label[:60]} {embed_flag}")

    conn.close()
    print(f"\nTerminé : {ok} retraité(s), {failed} échec(s).")


if __name__ == "__main__":
    main()
