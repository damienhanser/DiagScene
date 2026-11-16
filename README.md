# DiagScene — extension Blender pédagogique

DiagScene est née d'un besoin d'enseignement : aider les étudiants en synthèse d'image, en école d'architecture, à devenir autonomes sur leurs propres fichiers. Plutôt que de corriger à leur place, elle **pointe** ce qui ne va pas, explique **pourquoi** c'est un problème et **comment** le corriger. C'est l'étudiant qui corrige, et qui apprend en le faisant.

L'extension lit votre fichier Blender et **pointe ce qui ne va pas** : réglages de rendu, caméras, lumières, échelles, matériaux, textures, noms et rangement. **Elle ne modifie jamais rien** : c'est vous qui corrigez, en suivant ses explications.

## Installer (une seule fois)
1. Télécharger le fichier `diagscene-<version>.zip` depuis la page [Releases](https://github.com/damienhanser/DiagScene/releases) (ne pas le décompresser). Pour mettre à jour, installer le nouveau `.zip` de la même façon.
2. Dans Blender : *Édition > Préférences > Add-ons*, menu flèche en haut à droite, **Installer depuis le disque…**, choisir le `.zip`.
3. Vérifier que « DiagScene » est coché.

Blender 4.2 ou plus récent.

## Utiliser
1. Enregistrer son fichier `.blend`.
2. Dans la vue 3D, touche **N**, onglet **DiagScene**.
3. Choisir la thématique du cours (par exemple « Rendu réaliste »), puis **Analyser le fichier**.
4. Lire les alertes :
   - ⊗ **Bloquant** : à corriger avant tout rendu ou toute remise.
   - ⚠ **Avertissement** : un défaut qui se verra ou qui gênera.
   - ⓘ **Conseil** : une bonne pratique (noms, rangement, réglages).
   - ✓ **Acquis** : une technique attendue que vous utilisez déjà.
5. Le petit triangle ouvre le **pourquoi** et le **comment corriger** ; la flèche de sélection **montre** l'objet ou le matériau en cause.
6. **Exporter le rapport (.md)** : un rapport daté est enregistré dans un dossier `rapports/` à côté du fichier. En exportant à chaque étape, le tableau « Évolution » en bas du rapport montre vos progrès.

## Thématiques
- **Rendu réaliste (Master DNA/VDA)** : rendu architectural réaliste.
- **Vérification avant le bake** : sélectionner l'objet à baker, puis analyser juste avant de lancer le bake.
- **Général** : contrôles de base pour tout fichier.

Conseil : mettre son nom dans le nom du fichier `.blend` ; il se retrouve dans le nom et l'en-tête du rapport.

## Pour l'enseignant : adapter les règles
Les règles se règlent dans un fichier TOML par thématique (`configs/` dans l'extension) : activer ou désactiver une règle, changer son niveau, ses seuils et ses textes. `configs/general.toml` liste toutes les règles et tous leurs réglages. Un fichier personnalisé peut aussi être choisi directement dans le panneau (« Fichier personnalisé… »).

Licence : GPL-3.0-or-later. Auteur : Damien Hanser.
