# Azurik Level Studio 2.0.1

**Version 2.0.1** de l’éditeur de niveaux d’**Azurik: Rise of Perathia**, pour votre propre copie du jeu Xbox.

[Télécharger pour Windows](https://github.com/AzurikPerathia/Azurik-Level-Editor/releases/tag/v2.0.1) · [English guide](README.en.md) · [Illustrations](docs/SCREENSHOTS.md) · [Prochaine version](ROADMAP.md)

![Menus de l’éditeur version 2.0.0](docs/screenshots/v2-editor-menus.jpg)

## Correctif 2.0.1

Les calculs d’éclairage, de réflexion et de limites des objets transparents sont réutilisés quand ils restent identiques, sans réduire le détail ni les textures. Sur un test local de mise à jour des matières d’A5, le temps processeur passe de **4,19 à 1,16 ms par image** (environ 72 % de moins) ; cette mesure ne représente pas les FPS du GPU. La cadence des textures dans une vue immobile est désormais distinguée des FPS pendant le déplacement. Les projets et les données du jeu sont conservés.

## Nouveautés 2.0.0

- Caméra libre par défaut, commandes et focus de la vue corrigés, regard sur place au clic droit ou avec les flèches.
- Menus visibles : fichier, édition, importation, affichage et aide aux raccourcis.
- **Restaurer le niveau** revient à l’état source du niveau actif en une opération qui peut elle-même être annulée.
- **Ctrl Z / Ctrl Maj Z** pour annuler/rétablir transformations, imports, remplacements, duplication et restauration.
- Import et remplacement de modèles statiques **OBJ, JSON de géométrie, glTF/GLB**, et duplication indépendante.
- Import de textures **PNG**, affectation aux modèles importés et remplacement des surfaces natives compatibles.
- Réduction des transferts répétés et du travail inutile dans la vue ; le décodage des gros niveaux prend encore du temps.
- Votre image **Perathia Modding Hub** devient l’icône de l’exécutable et de la fenêtre 2.0.0.

Les remplacements compatibles sont exportables dans les archives du jeu. Les nouveaux modèles, duplications et textures sans cible native restent des **aperçus du projet**, enregistrés et exportés avec leurs fichiers. [Règles précises](docs/EDITING_V2.md).

## Tester les modifications en jeu

EDIT : You need to clear the cache for the changes you make to take effect ! Don’t worry, this won’t affect your save files in any way!

Il s’agit **uniquement du cache Xbox** : **Clear Cache** dans xemu-dashboard ou **Flush Cache Partitions** dans LithiumX, puis redémarrage sur l’ISO modifiée. Conservez le disque dur virtuel et la partition E contenant les sauvegardes. [Instructions et références Xemu](docs/GAME_CACHE.md).

## Ouvrir le logiciel

1. Téléchargez **Azurik-Level-Studio-2.0.1-Windows-x64.zip** et extrayez-le.
2. Double-cliquez sur **Azurik Level Studio.exe**. L’exécutable inclut Python et les dépendances : aucune installation de Python n’est nécessaire.
3. Cliquez sur **Importer un ISO**, choisissez votre ISO/XISO Xbox d’Azurik ou saisissez son chemin. Attendez l’import, puis choisissez le niveau.
4. Sélectionnez un élément, déverrouillez-le si nécessaire, puis déplacez-le, tournez-le ou changez son échelle avec les outils ou les champs précis.
5. **Enregistrer** conserve le projet. **Exporter le mod** crée de nouveaux XBR avec un rapport, à intégrer dans une copie du jeu pour tester.

Dans les sources, l’exécutable est dans `windows/Azurik Level Studio.exe`. **Ouvrir Azurik Level Studio.bat** permet aussi de le lancer ; les lanceurs `.cmd` et PowerShell sont conservés. Le logiciel ouvre sa propre fenêtre. Windows x64, .NET Framework et le [runtime WebView2](https://developer.microsoft.com/microsoft-edge/webview2/) sont requis ; ce dernier est généralement installé.

La version compilée conserve projets, imports, cache, exports et journaux dans **`%LOCALAPPDATA%\AzurikLevelStudio`**. Elle réutilise un serveur de même version ; un serveur plus ancien reste ouvert et la nouvelle version utilise un port distinct. En mode source, les données restent dans le dossier du code.

Sauvegardez une copie de votre projet avant les opérations d’assets V2, qui mettent son format à jour en version 2. Fermez l’ancien éditeur avant de modifier le même projet.

## Fonctionnalités

- Décors, modèles placés, textures, matières et références en 3D ; montagnes et structures statiques ; ciel jour/nuit.
- 33 niveaux détectés dans le dump européen étudié ; la disponibilité suit votre disque.
- Interface français/anglais sans rechargement du niveau.
- Caméra orbitale ou libre, vues perspective/dessus/face/côté, cadrage et regard à 360° sur place.
- Déplacement, rotation, échelle, champs précis, annuler/rétablir et restauration du placement source.
- Verrouillage individuel ou du niveau, enregistré et contrôlé par le serveur.
- Bibliothèque d’assets avec recherche, séquences et cubemaps ; export trié PNG/glTF avec l’outil fourni.
- Import ISO local, progression, validation et projets séparés.
- Captures PNG de la vue enregistrées dans le projet.
- Véritable exécutable Windows, icône, version et empreinte SHA-256.

## Commandes

| Action | Commande |
| --- | --- |
| Orbiter / zoomer | Clic gauche + glisser / molette |
| Cadrer la sélection / le niveau | F / Home |
| Avancer / gauche / reculer / droite | **Z / Q / S / D** en français ; **W / Q / S / D** en anglais |
| Regarder sans se déplacer | Clic droit + glisser ou flèches en caméra libre |
| Monter / descendre | Espace / Ctrl |
| Vitesse | Lent / Normal / Rapide ; Maj accélère, Alt ralentit |
| Déplacer / tourner / redimensionner | W / E / R hors touches consommées par la caméra |
| Annuler / rétablir / enregistrer | Ctrl Z / Ctrl Maj Z (ou Ctrl Y) / Ctrl S |

Cliquez dans la vue avant de naviguer. Les lettres suivent le clavier, y compris AZERTY. Les champs de texte et dialogues suspendent les déplacements. La langue traduit l’interface ; les noms des ressources restent conservés.

## Export et limites

Le dump et l’ISO d’origine sont lus sans modification. Les placements vérifiés sont exportés dans de **nouveaux XBR** avec empreintes et rapport. Les autres transformations sont signalées **aperçu seulement**, enregistrées séparément dans `scene-overrides.json` et ne sont pas appliquées au jeu.

Les collisions restent à leur position originale quand un décor est déplacé : vérifiez le résultat en jeu. La reconstruction automatique d’une ISO complète n’est pas une fonction de l’éditeur 2.0.0.

Le rendu utilise les données du jeu sans exécuter tout le moteur Xbox. Personnages en pose de liaison, scripts, animations de squelette, particules, éclairages ponctuels et certains effets restent partiels. Ciel et chemin vérifié du brouillard/reflet à deux couches d’A5 sont pris en charge. Un rendu identique pixel par pixel n’est pas annoncé.

## Sources et prochaine version

Pour la fenêtre native : Python 3.10+, `python -m pip install -r requirements-desktop.txt`, puis `python desktop.py`. Utilisez `--source` pour un dump extrait. Le navigateur reste disponible avec `python server.py --open` après installation de `requirements.txt`.

Pour compiler : Windows x64, Python 3.11+ et **Build Windows.ps1**. Tests : `python -m pytest -q` ; Node.js est requis uniquement pour les tests de l’interface. Les fichiers du jeu ne sont pas inclus.

L’import, le remplacement et la duplication sont disponibles dans la 2.0.0 avec les limites d’export décrites dans [le guide V2](docs/EDITING_V2.md). Les ajouts arbitraires dans le jeu et les collisions restent dans [ROADMAP.md](ROADMAP.md).

[Dépôt dédié](https://github.com/AzurikPerathia/Azurik-Level-Editor) · [Expensions](https://github.com/AzurikPerathia/Elemental_Games_Modding---Expensions) · [Pull request en anglais](https://github.com/JTCPP/Elemental_Games_Modding/pull/2)
