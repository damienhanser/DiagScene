# DiagScene — journal des versions

## v1.0.0 — 2026-11-16
Première version, préparée pour la séance Blender DNA/VDA du 2026-11-20.
- 17 règles : échantillons, résolution, caméra active, caméra d'aplomb, ombres dures, résolution de la HDRI, échelle non appliquée, échelle négative, visibilité écran/rendu, objet sans matériau, nodes non reliés, textures introuvables, espace colorimétrique, cartes UV manquantes, déplacement en bump, noms par défaut et copies, objets hors collection.
- Trois niveaux : bloquant, avertissement, conseil. Aucune correction automatique.
- Configurations livrées : « Rendu réaliste (Master DNA/VDA) » et « Général ».
- Bouton « Montrer » (sélection de l'élément en cause), export Markdown daté dans `rapports/`, tableau d'évolution entre rapports.
- Validé par `blender --command extension validate`, construit avec `extension build` (Blender 5.3.0) ; version minimale déclarée : 4.2.0.

## v1.1.0 — 2026-11-16
Élargie après le cadrage des critères avec Damien (lumière, UV, bake, caméras après fSpy, matériaux).
- **44 règles** en sept familles : rendu, caméras, lumière, objets, matériaux, UV, bake, organisation.
- **Nouveau niveau « acquis »** : les techniques attendues (Noise, Color Ramp, Mix piloté par une texture) sont signalées quand elles sont utilisées.
- **Caméras** : focale hors plage, caméra mise à l'échelle, format désaccordé de la photo de fond (fSpy), photo introuvable, découpe, décalage excessif ; l'aplomb ignore les caméras calées sur une photo.
- **Lumière** : lampe enfermée dans un volume (lancer de rayons), puissance hors plage (lampes et monde), exposition qui compense, ombres désactivées, lampes identiques non liées, couleur sans température (une alerte par lampe partagée).
- **Échelle non appliquée** : dimensions réelles affichées ; bloquant quand un modificateur ou une texture en coordonnées Objet/Générées en dépend.
- **Matériaux** : déplacement adapté à la distance de la caméra (remplace « déplacement en bump », qui allait à contre-sens), vrai déplacement sans subdivision, matériau uniforme sur une grande surface.
- **UV** (seuils à calibrer) : UV non dépliées (totalement ou en partie), étirement, échelle de texture incohérente entre objets, plusieurs cartes UV, chevauchements et sortie du cadre (actifs pour le bake). Mesures faites matériau par matériau, sur les seules faces dont le matériau utilise des UV.
- **Bake** : nouvelle thématique « Vérification avant le bake » (seulement les règles listées) : sélection, moteur, image cible, image non enregistrée, espace colorimétrique, marge, éclairage cuit, extrusion.
- Rapport : section Acquis, colonne Acquis dans l'évolution.

## Renommage — 2026-11-16
Avant toute diffusion : l'extension « Diagnostic de fichier » (`diagnostic_fichier`) devient **DiagScene** (`diagscene`), dépôt https://github.com/damienhanser/DiagScene.

## v1.2.0 — 2026-11-21
Traçabilité et évaluations externes.
- **Journal de travail** dans le fichier .blend (bloc de texte caché « .diagscene_journal ») : une ligne à chaque enregistrement et à chaque analyse (date, version de Blender, objets, faces, matériaux, images, poids du fichier, empreinte anonyme de la machine, thématique et empreinte de la configuration). Lignes chaînées par empreinte : une ligne modifiée ou supprimée se repère. Désactivable dans les préférences de l'extension. Sert à retrouver l'arrivée d'un problème (projets personnels) et d'indice d'authenticité (évaluations).
- **Rapport** : section « Journal de travail (historique du fichier) », empreinte de la configuration dans l'en-tête.
- **Configurations externes** : « Charger un fichier .toml… » ; le dernier fichier chargé est mémorisé dans les préférences et proposé en tête de liste. Les grilles d'évaluation ne sont plus livrées avec l'extension : elles sont fournies à part par l'enseignant, hors du dépôt.
- **Nouvelles règles** (inactives par défaut, à activer par configuration) : `cameras_nommees` (nombre minimal de caméras nommées), `rendu_non_realiste` (Freestyle, Line Art, Filaire, coque inversée, Shader vers RVB, Toon ; « acquis » quand présent), `journal_travail` (journal absent, altéré, trop peu de jours ou d'enregistrements).
- **Poids et géométrie** (actives en « conseil » dans Général) :
  - `texture_surdimensionnee` : résolution de chaque image comparée à ce que les caméras peuvent en afficher (pixels par mètre à l'écran × densité de texels des UV, répétitions du node Mapping comprises) ; propose une taille suffisante ;
  - `densite_polygones` : faces par pixel de l'objet à l'image, objets hors champ, total de la scène instances comprises (seuil 3 millions) ;
  - `copies_non_liees` : objets identiques (géométrie, échelle, matériaux, modificateurs) aux maillages séparés ;
  - `donnees_inutilisees` : images gardées sans servir au rendu (nodes non reliés), données gardées par le seul bouclier, matériaux attribués à aucune face ;
  - `geometrie` : sommets en double, normales incohérentes ou inversées (volumes fermés), faces non planes ;
  - `poids_fichier` : poids du fichier, faces, mémoire des textures (information, avertissement au-delà de seuils facultatifs).
- **UV** : option `seulement_textures_image` pour `uv_manquantes` et `uv_ecrasees` (ne contrôler que les objets dont une texture image est placée par des UV).
- Première grille externe : modélisation avancée (M1, S7), distribuée à part aux étudiants.

## v1.2.1 — 2026-11-21
- **Journal imposé par la grille** : une grille chargée depuis un fichier peut contenir `[journal]` avec `obligatoire = true`. Le journal est alors toujours tenu, et la case des préférences est remplacée par « imposé », avec le nom de la grille. L'obligation dure tant que cette grille reste la dernière chargée, y compris après un redémarrage de Blender. Le panneau affiche « imposé » à côté du journal.
- **Versions de cours** : un paquet peut intégrer une grille unique (`configs/verrou.toml`). Elle est alors la seule thématique proposée, sans « Charger un fichier .toml… », et son éventuelle section `[journal]` s'applique. La version publique n'en contient pas.
