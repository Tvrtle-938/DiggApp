# 🎯 Plan DiggApp v2 — soutenance du lundi 7 septembre 2026

Document de travail. Chaque étape se colle telle quelle dans Claude Code, se teste, se
commite, puis on passe à la suivante.

---

## Contexte à garder en tête en permanence

Soutenance du **Bloc 2** du titre RNCP39974 (Responsable de projet webmarketing et
communication digitale) : *« Produire et gérer des contenus digitaux vecteurs d'engagement
et responsables »*. L'étude de cas est DiggApp.

| Compétence | Ce qui la valide |
|---|---|
| C2.1 Messages engageants adaptés à la cible et au canal | Posts générés par le Studio **puis édités par moi**, selon une ligne éditoriale que j'ai définie |
| C2.2 Contenus textuels de qualité (storytelling, copywriting) | Idem, avec mes choix rédactionnels défendus à l'oral |
| C2.3 Déterminer l'objet et le format d'un contenu visuel/audio | Scripts de tournage et prompts IA générés par le Studio |
| C2.4 Réaliser des contenus visuels/audios | La vidéo réellement tournée ou réellement générée, **montée et sous-titrée par moi** |
| C2.5 Produire du contenu via un outil de gestion/publication | Le Studio (générer, éditer, marquer publié) + l'édition manuelle des catégories |
| C2.6 Ergonomie, navigation, RGPD, accessibilité | Nouvelle interface (sidebar, pages), alt text, mention de confidentialité, navigation clavier |

**Le référentiel évalue des contenus digitaux PUBLIÉS EN LIGNE.** L'app est l'outil, les
contenus sont les preuves. D'où la règle : **arrêt du code samedi midi**, le reste du
week-end = production, publication, prépa de l'oral.

---

## Séquence

| Quand | Étape | Compétence |
|---|---|---|
| Jeudi soir | 0. Commit git + 1. Gemini manager | fiabilité |
| Vendredi matin | 2. Shell sidebar + pages | C2.6 |
| Vendredi après-midi | 3. Accueil chat-first | C2.6 |
| Vendredi soir | 4. Édition des catégories | C2.5 |
| Samedi matin | 5. Ligne éditoriale + agent qui agit / 6. Accessibilité | C2.1, C2.3, C2.5, C2.6 |
| **Samedi midi** | **STOP CODE** | |
| Samedi aprèm → dimanche | 7-11. Production + publication | **C2.1 à C2.4** |
| Dimanche soir | 12-14. Prépa oral | |

Commiter après chaque étape validée : `git add -A && git commit -m "étape X : ..."`

---

## Étape 0 — Sécuriser l'existant

```bash
cd ~/Documents/hub-ia
source venv/bin/activate
git add -A
git commit -m "Studio de contenu : posts, scripts et prompts IA depuis la collection"
```

---

## Étape 1 — Gemini manager + synonymes

```
Projet DiggApp (~/Documents/hub-ia) : app de curation personnelle, Flask + bot Telegram,
SQLite, IA locale (Ollama) et cloud (Gemini).

OBJECTIF : faire de Gemini (cloud) le moteur de raisonnement par défaut partout où il y a du
jugement, en gardant les modèles locaux en secours et pour la perception uniquement.

CONTEXTE DU PROBLÈME : aujourd'hui agent.py essaie le modèle local (llama3.2, 3B) en premier.
Résultat constaté : sur "je veux une sélection de t-shirts manches longues", l'agent a appelé
search_by_category("mode") et listé TOUTE la catégorie mode (sneakers, sandales, veste de
sport inclus) au lieu de filtrer sur le sujet réel. Le modèle local est trop faible pour ce
type de jugement.

À FAIRE :
1. Dans agent.py, fonction chat() : inverser la priorité. Gemini (_agent_loop_gemini) en
   premier par défaut, repli sur Ollama local (_agent_loop_ollama) seulement si le cloud
   échoue. EXCEPTION : si ai_engine.get_engine_mode() == "local", garder le local en premier
   (l'utilisateur force explicitement le local via la commande Telegram /moteur local).
2. NE PAS toucher à ai_engine.analyze() (classification des captures) ni à embeddings.py : la
   perception locale (qwen2.5vl pour la vision, nomic-embed-text pour les embeddings) reste
   inchangée.
3. Dans content_studio.py, ajouter dans les TROIS constructeurs de prompt
   (_build_post_prompt, _build_script_prompt, _build_ai_prompt_prompt) cette règle :
   "Reconnais les synonymes et équivalents français/anglais du domaine avant de juger la
   pertinence d'un élément (ex. 'manches longues' = 'longsleeve'/'long sleeve', 'baskets' =
   'sneakers', 'sweat à capuche' = 'hoodie') : un élément qui utilise le terme anglais n'est
   pas hors sujet pour une demande formulée en français."
4. Ajouter un log clair (logger.info) indiquant quel moteur a répondu ("cloud" ou "local"),
   dans agent.chat() et dans content_studio._generate_raw(), pour le vérifier en démo.

CONTRAINTES :
- Aucune nouvelle dépendance.
- bot.py et web.py doivent continuer à fonctionner à l'identique par ailleurs.
- Commentaires en français, dans le style du projet.
- Ne pas modifier templates/index.html dans cette étape.

VALIDATION :
- Lancer web.py, demander dans le chat "je veux une sélection de t-shirts manches longues"
  → la réponse doit citer uniquement les articles manches longues, y compris le lien TikTok
  taggé "longsleeve", et exclure sneakers/sandales/veste de sport.
- Le terminal doit montrer que c'est le moteur cloud qui a répondu.
```

---

## Étape 2 — Shell : sidebar + pages pleine page

```
Projet DiggApp (~/Documents/hub-ia). Refonte de l'interface web (templates/index.html,
fichier unique, vanilla JS, pas de framework, pas d'étape de build).

OBJECTIF : passer d'une page unique avec panneaux flottants à une structure type Claude
Desktop : une barre latérale gauche et une zone principale qui affiche une seule page à la
fois.

STRUCTURE CIBLE :
- Sidebar gauche fixe (~240px sur desktop) avec le logo DiggApp en haut et 3 destinations :
  1. "Digg" (le chat) — page par défaut
  2. "Collection" (la base de données)
  3. "Studio" (la création de contenu)
- Zone principale : affiche UNE page à la fois, en plein espace disponible.
- Mobile (< 700px) : la sidebar devient une barre de navigation en bas, ou un menu
  hamburger — au choix, le plus simple et le plus fiable.

CONTENU DES PAGES (déplacer l'existant, ne rien réécrire) :
- "Collection" : la barre de recherche, les chips de catégories, le toggle Images/Liens et
  la grille de cartes qui existent déjà aujourd'hui sur la page d'accueil.
- "Studio" : le contenu du panneau flottant Studio actuel (formulaire de génération +
  liste des brouillons), en pleine page cette fois.
- "Digg" : pour l'instant, le contenu du panneau de chat actuel, en pleine page. L'accueil
  chat-first définitif sera fait à l'étape suivante.

CONTRAINTES :
- Garder la palette et les variables CSS existantes (thème "excavation" brun/ambre).
- Supprimer les deux boutons flottants (chat-fab et studio-fab) devenus inutiles.
- Toutes les fonctionnalités actuelles doivent continuer à marcher : filtres, recherche,
  chat, génération de brouillons, édition/publication/suppression des brouillons.
- Navigation en JS simple (afficher/masquer les sections), pas de router ni de framework.
- Garder le footer de mention de confidentialité, accessible depuis au moins une page.

VALIDATION :
- Les 3 pages s'affichent et se remplacent correctement, sur desktop et en fenêtre étroite.
- Aucune régression : je peux filtrer ma collection, discuter avec l'agent, générer un
  brouillon et le marquer publié.
```

---

## Étape 3 — Accueil chat-first « Demander à Digg »

```
Projet DiggApp (~/Documents/hub-ia), suite de la refonte de templates/index.html.

OBJECTIF : transformer la page "Digg" en accueil conversationnel, dans l'esprit des pages
d'accueil de Claude / ChatGPT / Gemini.

ÉTAT INITIAL DE LA PAGE (avant toute question) :
- Zone principale vide et aérée, contenu centré verticalement.
- Une bulle de saisie large au centre, avec le placeholder en transparence :
  "Demander à Digg"
- Éventuellement 2-3 suggestions cliquables sous la bulle (ex. "Fais-moi une sélection de
  manches longues", "Qu'est-ce que j'ai sauvegardé en déco ?", "Crée un post X sur mes
  dernières trouvailles") qui remplissent la saisie au clic.

APRÈS ENVOI D'UNE QUESTION :
- Transition fluide : la bulle centrale se déplace vers le bas de l'écran, la conversation
  s'affiche au-dessus (messages utilisateur à droite, réponses de l'agent à gauche).
- Pendant que l'agent réfléchit : une animation d'attente discrète (pulsation, points
  animés — pas de spinner générique).
- Les éléments de la collection cités par l'agent s'affichent en vignettes sous sa réponse
  (le rendu mini-cartes existe déjà dans le code actuel du chat, le réutiliser).

CONTRAINTES :
- Même palette et mêmes variables CSS que le reste de l'app.
- Vanilla JS, fichier unique, aucune dépendance externe.
- L'API utilisée reste /api/chat, ne pas modifier le backend.
- Les animations doivent respecter prefers-reduced-motion.
- L'input doit faire au moins 16px de police (sinon iOS zoome automatiquement).

VALIDATION :
- L'accueil est épuré, la bulle "Demander à Digg" est bien au centre.
- Une question déclenche l'animation puis affiche la réponse et les vignettes.
- Fonctionne aussi sur téléphone (via l'IP locale, même wifi).
```

---

## Étape 4 — Édition manuelle des catégories

```
Projet DiggApp (~/Documents/hub-ia).

OBJECTIF : pouvoir reclasser moi-même un élément de la collection, sans passer par l'IA.

À FAIRE :
1. Backend (web.py) : nouvelle route PUT /api/items/<int:item_id> acceptant un JSON
   {"category": "<une des catégories valides>"}. Valider la catégorie contre
   ai_engine.VALID_CATEGORIES, refuser proprement sinon.
2. IMPORTANT — après un changement de catégorie, régénérer l'embedding de l'élément, sinon
   la recherche sémantique reste calée sur l'ancienne catégorie. Réutiliser
   embeddings.build_item_text() puis embeddings.get_embedding() et mettre à jour la colonne
   embedding. Si Ollama est indisponible, ne pas bloquer la mise à jour de la catégorie :
   loguer un avertissement.
3. Frontend (templates/index.html, page Collection) : au clic sur le badge catégorie d'une
   carte, afficher un petit sélecteur des catégories disponibles ; à la sélection, appeler
   l'API et mettre à jour la carte et les compteurs de chips sans recharger la page.

CONTRAINTES :
- Pas de nouvelle dépendance, vanilla JS.
- Le sélecteur doit être utilisable au clavier (focus visible, échappement pour fermer).
- Cohérence visuelle avec le reste de l'interface.

VALIDATION :
- Je clique sur le badge "mode" d'un élément, je choisis "sport", la carte se met à jour.
- Après changement, une recherche sémantique sur ce sujet retrouve bien l'élément dans sa
  nouvelle catégorie (l'embedding a été régénéré).
```

---

## Étape 5 — Ligne éditoriale paramétrable + agent qui agit

```
Projet DiggApp (~/Documents/hub-ia).

OBJECTIF (double) : rendre la ligne éditoriale paramétrable par moi, et permettre à l'agent
d'agir sur les données depuis le chat.

PARTIE A — LIGNE ÉDITORIALE
1. Stocker une ligne éditoriale modifiable : cible/persona, ton, engagements RSE.
   Table SQLite "settings" (clé/valeur) ou fichier JSON — au plus simple.
2. Routes web.py : GET et PUT /api/editorial-line.
3. Interface : un petit panneau "Ligne éditoriale" en haut de la page Studio, avec 3 champs
   texte et un bouton Enregistrer.
4. content_studio.py : remplacer la constante DEFAULT_PERSONA codée en dur par la valeur
   enregistrée, injectée dans les trois constructeurs de prompt (post, script, prompt IA).
   Garder la valeur actuelle comme défaut si rien n'est encore paramétré.

PARTIE B — AGENT QUI AGIT
5. Ajouter à agent.py deux nouveaux outils, en plus des outils de recherche existants :
   - generate_content(topic, content_type, target) : appelle content_studio.generate_draft()
     et renvoie le brouillon créé. Permet de demander dans le chat "crée-moi un post X sur
     mes manches longues" ou "fais-moi un script TikTok là-dessus".
   - move_item_to_category(item_id, category) : reclasse un élément (réutiliser la logique
     de l'étape 4, régénération de l'embedding comprise).
6. Compléter le SYSTEM_PROMPT pour expliquer quand utiliser ces outils, en gardant les
   règles existantes (ne jamais inventer d'élément, ne pas lister toute une catégorie).

CONTRAINTES :
- Ne pas casser le Studio ni le bot Telegram.
- Aucune nouvelle dépendance.

VALIDATION :
- Je modifie ma ligne éditoriale, je génère un post : le ton et l'angle reflètent ce que j'ai
  écrit.
- Dans le chat : "crée-moi un post X sur mes t-shirts manches longues" crée bien un brouillon
  visible dans le Studio.
- Dans le chat : "range l'élément 5 en catégorie sport" reclasse bien l'élément.
```

---

## Étape 6 — Passe accessibilité et RGPD

```
Projet DiggApp (~/Documents/hub-ia). Dernière étape de code.

OBJECTIF : mettre l'interface en conformité avec les exigences d'accessibilité (RGAA) et de
protection des données (RGPD) — c'est explicitement évalué dans mon référentiel.

À FAIRE :
1. Navigation clavier : tous les éléments interactifs (liens, boutons, sélecteurs, onglets de
   la sidebar) atteignables au Tab, avec un focus visible et contrasté. Ajouter un lien
   d'évitement "Aller au contenu principal" en début de page.
2. Sémantique : utiliser nav / main / section, des titres hiérarchisés (un seul h1 par page),
   et des aria-label sur tous les boutons qui n'ont qu'une icône ou un emoji.
3. Images : vérifier que toutes les images de contenu ont un alt descriptif (déjà fait pour
   la grille et le chat, vérifier les nouvelles vues).
4. Contrastes : vérifier que le texte secondaire (--text-dim sur --bg et sur --card) atteint
   au moins 4.5:1. Ajuster la variable si nécessaire.
5. Animations : respecter prefers-reduced-motion partout.
6. RGPD : une page ou section "Confidentialité" accessible depuis la sidebar, expliquant
   quelles données sont stockées (captures, liens, notes), où (base SQLite locale hub.db),
   pourquoi, ce qui est envoyé aux moteurs IA (Ollama en local, Gemini en cloud selon le
   mode), et comment supprimer un élément.

CONTRAINTES :
- Aucune régression visuelle majeure, on garde l'identité du thème.
- Vanilla JS, pas de dépendance.

VALIDATION :
- Je peux naviguer dans toute l'app au clavier uniquement.
- La section confidentialité est lisible et honnête.
```

---

## Étapes 7 à 11 — Production de contenu (samedi après-midi → dimanche)

Ce n'est plus du code. C'est ce qui valide réellement C2.1 à C2.4.

7. **Écrire ma ligne éditoriale** dans l'app : qui je vise, quel ton, quels engagements RSE
   (piste : consommation réfléchie, réemploi de contenus déjà sauvegardés plutôt que
   génération à outrance, accessibilité des contenus).
8. **Alimenter la collection** sur 2-3 thèmes exploitables (assez d'éléments pour que les
   sélections soient crédibles).
9. **Générer, éditer, publier du texte** : au minimum un post X et une légende Instagram, si
   possible un post LinkedIn. Garder une capture AVANT/APRÈS mon édition — c'est la preuve
   que la rédaction finale est la mienne.
10. **Produire un contenu visuel/audio** : soit tourner une vidéo depuis un script généré,
    soit passer un prompt généré dans Kling / Higgsfield / Grok Imagine, récupérer le rendu,
    puis **monter et sous-titrer moi-même** (les sous-titres sont un point d'accessibilité
    explicitement demandé par C2.3). Publier.
11. **Screenshots de tout** : brouillons générés, versions éditées, publications en ligne,
    interface de l'app.

## Étapes 12 à 14 — Prépa de l'oral (dimanche soir)

12. Tableau compétence ↔ preuve concrète (reprendre celui en haut de ce document en le
    remplissant avec mes contenus réels).
13. Déroulé de la démo live : ce que je montre, dans quel ordre, en combien de temps.
14. Questions du jury à anticiper. La plus probable et la plus importante :
    **« Qu'est-ce qui est de vous et qu'est-ce qui est de l'IA ? »**
    Réponse à préparer : j'ai conçu l'outil, défini la ligne éditoriale et la cible, choisi
    les formats et les canaux, et j'édite/valide chaque contenu avant publication. L'IA est
    un accélérateur de production piloté par mes décisions — ce qui est exactement le métier
    de responsable de projet en communication digitale aujourd'hui.
