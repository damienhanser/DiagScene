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
