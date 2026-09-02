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

# 4. Lancer Claude Code (dans un AUTRE onglet)
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
├── .env                ← Les secrets (token Telegram, futures clés API)
├── .gitignore          ← Protège les secrets et fichiers inutiles
├── requirements.txt    ← Liste des briques Python à installer
├── hub.db              ← La base de données SQLite (créée au 1er lancement)
├── venv/               ← L'environnement virtuel (caisse à outils Python)
├── data/
│   └── captures/       ← Les images reçues via Telegram
└── CHEATSHEET.md       ← Ce fichier !
```
