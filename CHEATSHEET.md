# 🧠 Hub-IA — Cheat Sheet Terminal

## ⚡ Routine quotidienne

```bash
# 1. Aller dans le projet
cd ~/Documents/hub-ia

# 2. Activer le venv (À FAIRE à chaque nouvel onglet/terminal)
source venv/bin/activate
# → tu vois (venv) apparaître au début de la ligne = c'est bon

# 3. Lancer le bot Telegram
python3 bot.py
# → Ctrl + C pour l'arrêter

# 4. Lancer l'interface web (dans un AUTRE onglet, venv activé aussi)
python3 web.py
# → puis ouvrir http://localhost:5001 dans le navigateur

# 5. Lancer Claude Code (dans encore un AUTRE onglet)
claude
# → /exit ou Ctrl + D pour quitter Claude Code
```

> **Règle d'or** : si `(venv)` n'apparaît pas au début de ta ligne → rien ne marchera. Toujours activer le venv en premier.

---

## 📁 Navigation dans le terminal

| Commande | Ce que ça fait |
|---|---|
| `cd ~/Documents/hub-ia` | Aller dans le dossier du projet |
| `cd ..` | Remonter d'un cran |
| `ls` | Lister les fichiers visibles |
| `ls -la` | Lister TOUS les fichiers (y compris .env, .gitignore) |
| `pwd` | Afficher le chemin du dossier actuel |
| `open .` | Ouvrir le dossier actuel dans le Finder |

---

## 📝 Lire et éditer des fichiers

| Commande | Ce que ça fait |
|---|---|
| `cat bot.py` | Afficher le contenu d'un fichier |
| `nano .env` | Éditer un fichier dans le terminal |
| | → `Ctrl + O` puis `Entrée` = sauvegarder |
| | → `Ctrl + X` = quitter nano |
| `open -a TextEdit .env` | Ouvrir un fichier caché dans TextEdit |

---

## 🪟 Onglets terminal (Mac)

| Raccourci | Ce que ça fait |
|---|---|
| `Cmd + T` | Nouvel onglet |
| `Cmd + Shift + [` ou `]` | Naviguer entre les onglets |
| `Cmd + W` | Fermer l'onglet actuel |

---

## 📦 Python / pip (gestion des briques)

```bash
pip3 install -r requirements.txt   # Installer toutes les briques du projet
pip3 install nom_du_paquet         # Installer une brique spécifique
pip3 list                          # Voir les briques installées
pip3 uninstall nom_du_paquet       # Désinstaller une brique
```

---

## 🗄️ SQLite (consulter ta base de données)

```bash
sqlite3 hub.db                     # Ouvrir la base
```

Une fois dedans :

| Commande SQLite | Ce que ça fait |
|---|---|
| `.tables` | Voir les tables existantes |
| `SELECT * FROM captures;` | Voir toutes les captures enregistrées |
| `SELECT * FROM captures ORDER BY created_at DESC LIMIT 5;` | Les 5 dernières captures |
| `SELECT COUNT(*) FROM captures;` | Combien de captures au total |
| `.schema captures` | Voir la structure de la table |
| `.quit` | Quitter SQLite |

---

## 🔐 Sécurité — rappels

- **Ne jamais** partager le contenu du `.env` (tokens, clés API)
- **Ne jamais** commit le `.env` sur Git (le `.gitignore` le protège)
- Si un token fuite → aller sur BotFather → `/revoke` → nouveau token

---

## 🏗️ Architecture du projet

```
hub-ia/
├── bot.py              ← Le code du bot Telegram
├── .env                ← Les secrets (token Telegram, clé Gemini)
├── .gitignore          ← Protège les secrets et fichiers inutiles
├── requirements.txt    ← Liste des briques Python à installer
├── hub.db              ← La base de données SQLite (créée au 1er lancement)
├── venv/               ← L'environnement virtuel (caisse à outils Python)
├── data/
│   └── captures/       ← Les images reçues via Telegram
├── web.py              ← Le serveur de l'interface web (DiggApp)
├── agent.py            ← L'agent conversationnel (recherche dans la collection)
├── content_studio.py   ← Le Studio : posts, scripts et prompts IA
├── ai_engine.py        ← Moteur IA unifié (Ollama local / Gemini cloud)
├── semantic_search.py  ← Recherche par similarité sur les embeddings
├── embeddings.py       ← Calcul des embeddings (nomic-embed-text)
├── reprocess.py        ← Retraitement des éléments déjà en base
├── templates/
│   └── index.html      ← Toute l'interface web (HTML + CSS + JS)
├── GUIDE_LANCEMENT.md  ← Comment lancer l'app au quotidien
├── PLAN_SOUTENANCE.md  ← Plan de travail avant la soutenance
└── CHEATSHEET.md       ← Ce fichier !
```

---

## 🛑 Arrêter un programme qui tourne

> ⚠️ **Piège Mac** : c'est **Ctrl (⌃) + C**, PAS **Cmd (⌘) + C**. Cmd + C = copier. La touche `ctrl` est en bas à gauche du clavier, à côté de `option`.

Il faut d'abord **cliquer dans l'onglet où le programme tourne** pour l'activer, puis faire Ctrl + C. Chaque programme s'arrête dans son propre onglet (le bot et le serveur web sont deux processus séparés).

```bash
# Si un onglet ne répond vraiment plus, tuer le processus depuis un autre onglet
pkill -f "python3 bot.py"    # arrête le bot Telegram
pkill -f "python3 web.py"    # arrête le serveur web

# Vérifier qu'il ne reste rien sur le port 5001
lsof -i :5001
# → aucune réponse = le serveur est bien arrêté
```

---

## 🌐 Interface web (DiggApp)

```bash
python3 web.py                 # lance le serveur (venv activé)
PORT=5002 python3 web.py       # si le port 5001 est déjà occupé
```

- Sur le Mac : **http://localhost:5001**
- Depuis le téléphone (même wifi) : `http://<ip-du-mac>:5001` — l'IP s'affiche au lancement
- Le port 5000 est pris par AirPlay sur macOS, d'où le 5001 par défaut

---

## 🦙 Ollama (l'IA en local)

```bash
ollama list                    # voir les modèles installés (et vérifier qu'Ollama répond)
ollama serve                   # démarrer Ollama s'il ne tourne pas
ollama pull nom_du_modele      # télécharger un modèle
```

Les 3 modèles utilisés par le projet :

- `qwen2.5vl:7b` — analyse des images et des liens au moment de la capture
- `nomic-embed-text` — embeddings, **indispensable** à la recherche sémantique
- `llama3.2` — agent conversationnel de secours (Gemini cloud est prioritaire)

> ⚠️ Si Ollama ne tourne pas quand tu envoies des captures au bot, elles sont stockées **sans embedding** et la recherche ne les retrouvera jamais. Réparation : `venv/bin/python reprocess.py --embeddings-only`

---

## 🔁 Retraiter la base

```bash
venv/bin/python reprocess.py                    # analyse les éléments sans catégorie
venv/bin/python reprocess.py --all              # tout retraiter (analyse IA + embeddings)
venv/bin/python reprocess.py --embeddings-only  # régénérer seulement les embeddings (rapide)
```

---

## 🤖 Commandes du bot Telegram

- `/start` — message d'accueil et rappel des commandes
- `/moteur` — voir le moteur IA actif ; `/moteur local | cloud | auto` pour le changer
- `/stats` — statistiques de la collection (par type et par catégorie)
- `/post <canal> <sujet>` — génère un post prêt à publier (ex. `/post twitter sneakers tendance`)

---

## 📝 Studio de contenu

L'onglet 📝 de l'interface web génère, à partir de ta collection : des **posts** (X, Instagram, LinkedIn, newsletter), des **scripts de tournage** (TikTok, Reels, Shorts) et des **prompts pour IA générative** (image ou vidéo). Les brouillons y sont éditables, puis marqués publiés.

```sql
-- Voir les brouillons générés (dans sqlite3 hub.db)
SELECT id, content_type, channel, status, topic FROM drafts ORDER BY created_at DESC;
```

---

## 🌿 Git — sauvegarder ton travail

```bash
git status                     # voir ce qui a changé
git add -A                     # préparer tous les changements
git commit -m "message clair"  # enregistrer une version
git log --oneline              # historique des versions
```

Vérifier que les secrets sont bien protégés :

```bash
git check-ignore -v .env       # dit si le fichier est ignoré, et par quelle règle
git ls-files | grep -i env     # liste ce que git suit DÉJÀ ← le point critique
```

> Un `.gitignore` ne protège que les fichiers **pas encore ajoutés**. Si un `.env` a été commité une fois, la clé reste dans l'historique même après coup — il faut alors la révoquer et en générer une nouvelle.

**Erreur `Unable to create '.git/index.lock': File exists`** : un processus git a planté et laissé un fichier verrou. Une fois certain qu'aucune commande git ne tourne :

```bash
rm -f .git/index.lock
```

---

## 🧭 Réflexes à retenir

- `(venv)` absent au début de la ligne → rien ne marchera. Toujours `source venv/bin/activate`.
- Un onglet = un programme. Le bot, le serveur web et Claude Code tournent chacun dans le leur.
- **Ctrl** pour agir sur le terminal (arrêter, sauvegarder dans nano), **Cmd** pour agir sur la fenêtre (onglet, copier, fermer).
- Flèche ↑ dans le terminal = rappeler la commande précédente (évite de tout retaper).
- `Tab` complète automatiquement un nom de fichier ou de dossier commencé.
- Commiter après chaque étape qui marche : c'est le seul vrai filet de sécurité.
