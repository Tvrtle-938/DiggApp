# 🚀 Guide de lancement — Hub IA / DiggApp

Ce guide explique comment démarrer l'appli au quotidien. Pour les bases du terminal (nano, cd, git...), voir [CHEATSHEET.md](CHEATSHEET.md).

## ✅ Prérequis (à vérifier une fois, ou après une longue pause)

1. **Ollama doit tourner** (moteur IA local). Dans un terminal :
   ```bash
   ollama serve
   ```
   Ou vérifie qu'il tourne déjà (icône dans la barre de menu macOS). S'il n'est pas lancé, l'appli bascule automatiquement sur Gemini (cloud) si `AI_ENGINE=auto` ou `cloud` dans le `.env`.

2. **Les modèles Ollama doivent être téléchargés** (une seule fois) :
   ```bash
   ollama pull qwen2.5vl:7b        # analyse des images/liens
   ollama pull nomic-embed-text    # embeddings pour la recherche sémantique
   ollama pull llama3.2            # agent conversationnel (recherche par chat)
   ```
   > `llama3.2` est indispensable pour que la recherche par chat (`agent.py`) fonctionne en local — `qwen2.5vl` ne supporte pas le tool calling.

3. **Le fichier `.env`** doit contenir `TELEGRAM_BOT_TOKEN` (pour le bot) et `GEMINI_API_KEY` (pour le secours cloud). `AI_ENGINE` définit le moteur par défaut (`local`, `cloud` ou `auto`).

---

## 🤖 Lancer le bot Telegram

```bash
cd ~/Documents/hub-ia
source venv/bin/activate
python3 bot.py
```

- Envoie une photo ou un lien au bot → il l'analyse et le range.
- Envoie un message texte → il cherche dans la collection (via l'agent).
- Commandes dans Telegram : `/start`, `/moteur` (voir/changer local·cloud·auto), `/stats`.
- `Ctrl + C` pour arrêter.

---

## 🌐 Lancer l'interface web (DiggApp)

Dans un **autre onglet de terminal** (le bot doit tourner à part si tu veux les deux en même temps) :

```bash
cd ~/Documents/hub-ia
source venv/bin/activate
python3 web.py
```

Puis ouvre **http://localhost:5001** dans le navigateur.

> Port 5001 par défaut car macOS occupe le 5000 avec AirPlay Receiver. Pour libérer le 5000 : Réglages système → Général → AirDrop et Handoff → désactiver "Récepteur AirPlay", puis relancer avec `PORT=5000 python3 web.py`.

**Depuis ton téléphone (même Wi-Fi)** : l'appli écoute sur `0.0.0.0`, donc accessible via l'IP locale du Mac, ex. `http://192.168.1.x:5001` (trouver l'IP : Réglages système → Wi-Fi → Détails).

---

## 🔁 Lancer les deux en même temps

Ouvre 2 onglets terminal (`Cmd + T`), active le venv dans chacun, puis :
- Onglet 1 : `python3 bot.py`
- Onglet 2 : `python3 web.py`

---

## 🧰 Commandes utiles

```bash
# Retraiter les éléments sans catégorie (analyse IA)
venv/bin/python reprocess.py

# Tout retraiter (analyse IA + embeddings) — utile après un changement de prompt IA
venv/bin/python reprocess.py --all

# Régénérer uniquement les embeddings depuis les métadonnées existantes (rapide,
# pas d'appel IA) — utile après avoir ajouté la recherche sémantique ou changé
# de modèle d'embedding
venv/bin/python reprocess.py --embeddings-only
```

---

## 🩺 Dépannage rapide

| Problème | Solution |
|---|---|
| `(venv)` n'apparaît pas dans le prompt | Relance `source venv/bin/activate` (à refaire à chaque nouvel onglet) |
| Le bot dit "TELEGRAM_BOT_TOKEN manquant" | Vérifie le `.env` (`nano .env`) |
| Recherche par chat ne répond pas / erreurs "does not support tools" | `ollama pull llama3.2` puis relance `ollama serve` |
| Recherche sémantique ne trouve rien sur d'anciens éléments | Lance `venv/bin/python reprocess.py --embeddings-only` pour générer leurs embeddings |
| Port 5001 déjà utilisé | `PORT=5002 python3 web.py` (ou trouve et arrête l'ancien processus) |
| Tout est lent / timeouts | Vérifie qu'Ollama tourne (`ollama list` doit répondre) ; sinon passe en cloud avec `/moteur cloud` dans Telegram |

---

## 📁 Où sont les données

- `hub.db` — base SQLite (toutes les captures + embeddings)
- `data/captures/` — images reçues
- `data/thumbnails/` — miniatures des liens
