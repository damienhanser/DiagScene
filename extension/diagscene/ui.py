# SPDX-License-Identifier: GPL-3.0-or-later
"""Interface : onglet DiagScene du panneau latéral de la vue 3D."""

import os
import textwrap

import bpy
from bpy.props import BoolProperty, CollectionProperty, EnumProperty, IntProperty, PointerProperty, StringProperty
from bpy.types import AddonPreferences, Operator, Panel, PropertyGroup

from . import config, journal, rapport, regles

ICONES = {"bloquant": "CANCEL", "avertissement": "ERROR", "conseil": "INFO", "acquis": "CHECKMARK"}
PERSO = "__perso__"
EXTERNE = "__externe__"
_items_configs = []  # référence gardée : Blender exige que la liste survive à l'appel


def _version_extension():
    try:
        import tomllib
        with open(os.path.join(os.path.dirname(__file__), "blender_manifest.toml"), "rb") as f:
            return tomllib.load(f).get("version", "?")
    except Exception:
        return "?"


def _prefs(context=None):
    try:
        return (context or bpy.context).preferences.addons[__package__].preferences
    except (KeyError, AttributeError):
        return None


def _toml_externe(context=None):
    """Chemin de la dernière configuration chargée depuis un fichier, s'il existe toujours."""
    pr = _prefs(context)
    chemin = bpy.path.abspath(pr.dernier_toml) if pr and pr.dernier_toml else ""
    return chemin if chemin and os.path.isfile(chemin) else ""


def items_configs(self, context):
    global _items_configs
    _items_configs = []
    if config.verrouille():
        meta = config.lister()[0][1]
        _items_configs = [(os.path.basename(config.VERROU), meta.get("nom", "Grille du cours"),
                           meta.get("description", ""), "LOCKED", 0)]
        return _items_configs
    ext = _toml_externe(context)
    if ext:
        try:
            meta = config.charger(ext)["meta"]
            nom = meta.get("nom", os.path.basename(ext))
        except Exception:
            nom = os.path.basename(ext) + " (illisible)"
        _items_configs.append((EXTERNE, f"{nom} (fichier chargé)", ext, "FILE", 0))
    _items_configs += [(os.path.basename(ch), meta.get("nom", os.path.basename(ch)),
                        meta.get("description", ch), "NONE", i + 1) for i, (ch, meta) in enumerate(config.lister())]
    _items_configs.append((PERSO, "Charger un fichier .toml…", "Choisir un fichier de configuration .toml "
                           "(par exemple celui fourni par votre enseignant)", "FILEBROWSER", 999))
    return _items_configs


def _maj_config_perso(self, context):
    """Un fichier choisi devient la configuration externe mémorisée, en tête de liste."""
    chemin = bpy.path.abspath(self.config_perso)
    pr = _prefs(context)
    if pr is not None and chemin and os.path.isfile(chemin):
        pr.dernier_toml = chemin
        self.config = EXTERNE


def _config_choisie(etat):
    if config.verrouille():
        return config.charger(config.VERROU)
    if etat.config == EXTERNE:
        chemin = _toml_externe()
        if not chemin:
            raise ValueError("Le fichier de configuration chargé est introuvable : rechargez-le.")
        return config.charger(chemin)
    if etat.config == PERSO:
        chemin = bpy.path.abspath(etat.config_perso)
        if not chemin or not os.path.isfile(chemin):
            raise ValueError("Choisissez un fichier de configuration .toml.")
        return config.charger(chemin)
    return config.charger(os.path.join(config.DOSSIER, etat.config))


class DIAG_Alerte(PropertyGroup):
    regle: StringProperty()
    famille: StringProperty()
    niveau: StringProperty()
    titre: StringProperty()
    cible_type: StringProperty()
    cible: StringProperty()
    detail: StringProperty()
    pourquoi: StringProperty()
    comment: StringProperty()
    lien: StringProperty()
    ouvert: BoolProperty(name="Détails", default=False)


class DIAG_Etat(PropertyGroup):
    config: EnumProperty(name="Thématique", description="Jeu de règles utilisé pour l'analyse", items=items_configs)
    config_perso: StringProperty(name="Configuration", subtype="FILE_PATH", update=_maj_config_perso)
    voir_journal: BoolProperty(name="Journal de travail", default=False)
    alertes: CollectionProperty(type=DIAG_Alerte)
    analyse_faite: BoolProperty(default=False)
    thematique: StringProperty()
    fichier: StringProperty()
    nb_bloquant: IntProperty()
    nb_avertissement: IntProperty()
    nb_conseil: IntProperty()
    nb_acquis: IntProperty()
    dernier_rapport: StringProperty()
    filtre: EnumProperty(name="Afficher", items=[
        ("TOUT", "Tout", "Toutes les alertes", "NONE", 0),
        ("bloquant", "", "Bloquants", "CANCEL", 1),
        ("avertissement", "", "Avertissements", "ERROR", 2),
        ("conseil", "", "Conseils", "INFO", 3),
        ("acquis", "", "Acquis", "CHECKMARK", 4),
    ])


def _lancer_analyse(context):
    etat = context.window_manager.diag
    cfg = _config_choisie(etat)
    alertes = regles.analyser(context.scene, context.view_layer, cfg)
    etat.alertes.clear()
    for a in alertes:
        item = etat.alertes.add()
        for k, v in a.items():
            setattr(item, k, v)
    etat.thematique = cfg["meta"].get("nom", "")
    etat.fichier = bpy.path.basename(bpy.data.filepath) or "(fichier non enregistré)"
    for n in regles.NIVEAUX:
        setattr(etat, f"nb_{n}", sum(1 for a in alertes if a["niveau"] == n))
    etat.analyse_faite = True
    try:
        journal.ajouter("analyse", thematique=cfg["meta"].get("nom", ""), config_sha=cfg["meta"].get("sha", ""),
                        bloquants=etat.nb_bloquant, avertissements=etat.nb_avertissement, conseils=etat.nb_conseil)
    except Exception as e:
        print(f"DiagScene : journal non mis à jour ({type(e).__name__} : {e})")
    return alertes, cfg


class DIAG_OT_analyser(Operator):
    bl_idname = "diag.analyser"
    bl_label = "Analyser le fichier"
    bl_description = "Analyser la scène selon la thématique choisie, sans rien modifier"

    def execute(self, context):
        try:
            alertes, cfg = _lancer_analyse(context)
        except Exception as e:
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}
        e = context.window_manager.diag
        self.report({"INFO"}, f"{e.nb_bloquant} bloquant(s), {e.nb_avertissement} avertissement(s), {e.nb_conseil} conseil(s)")
        return {"FINISHED"}


class DIAG_OT_exporter(Operator):
    bl_idname = "diag.exporter"
    bl_label = "Exporter le rapport (.md)"
    bl_description = "Refaire l'analyse et enregistrer le rapport en Markdown dans un dossier rapports/ à côté du fichier"

    def execute(self, context):
        try:
            alertes, cfg = _lancer_analyse(context)
            chemin = rapport.ecrire(alertes, cfg, _version_extension())
        except Exception as e:
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}
        context.window_manager.diag.dernier_rapport = chemin
        self.report({"INFO"}, f"Rapport enregistré : {chemin}")
        return {"FINISHED"}


class DIAG_OT_ouvrir_dossier(Operator):
    bl_idname = "diag.ouvrir_dossier"
    bl_label = "Ouvrir le dossier des rapports"

    def execute(self, context):
        dossier = os.path.join(os.path.dirname(bpy.data.filepath), "rapports")
        if not bpy.data.filepath or not os.path.isdir(dossier):
            self.report({"WARNING"}, "Aucun rapport exporté pour ce fichier.")
            return {"CANCELLED"}
        bpy.ops.wm.path_open(filepath=dossier)
        return {"FINISHED"}


def _selectionner(context, objets):
    vl = context.view_layer
    for o in vl.objects:
        o.select_set(False)
    visibles = [o for o in objets if o.name in vl.objects]
    for o in visibles:
        try:
            o.select_set(True)
        except RuntimeError:
            pass
    if visibles:
        vl.objects.active = visibles[0]
        if context.area and context.area.type == "VIEW_3D":
            try:
                bpy.ops.view3d.view_selected()
            except RuntimeError:
                pass
    return visibles


class DIAG_OT_montrer(Operator):
    bl_idname = "diag.montrer"
    bl_label = "Montrer"
    bl_description = "Sélectionner l'élément en cause (rien n'est modifié)"
    cible_type: StringProperty()
    cible: StringProperty()

    def execute(self, context):
        t, c = self.cible_type, self.cible
        if context.mode != "OBJECT":
            self.report({"WARNING"}, "Repassez en mode Objet pour sélectionner l'élément.")
            return {"CANCELLED"}
        if t in ("OBJET", "OBJETS"):
            objs = [bpy.data.objects.get(n) for n in c.split("\t")]
            if not _selectionner(context, [o for o in objs if o]):
                self.report({"WARNING"}, "Objet introuvable ou masqué.")
        elif t in ("MATERIAU", "MATERIAUX", "NODE"):
            noms = c.split("\t")
            mats = [bpy.data.materials.get(noms[0])] if t == "NODE" else [bpy.data.materials.get(n) for n in noms]
            mats = [m for m in mats if m]
            objs = [o for o in context.scene.objects if any(s.material in mats for s in getattr(o, "material_slots", []))]
            if _selectionner(context, objs) and mats:
                o = context.view_layer.objects.active
                for i, s in enumerate(o.material_slots):
                    if s.material == mats[0]:
                        o.active_material_index = i
                        break
                if t == "NODE" and mats[0].node_tree:
                    nt = mats[0].node_tree
                    node = nt.nodes.get(noms[1])
                    if node:
                        for n in nt.nodes:
                            n.select = False
                        node.select = True
                        nt.nodes.active = node
                        self.report({"INFO"}, f"Node « {node.label or node.name} » sélectionné : ouvrez l'éditeur de shaders.")
        elif t == "COLLECTION":
            def trouver(lc):
                if lc.collection.name == c:
                    return lc
                for k in lc.children:
                    r = trouver(k)
                    if r:
                        return r
            lc = trouver(context.view_layer.layer_collection)
            if lc:
                context.view_layer.active_layer_collection = lc
                self.report({"INFO"}, f"Collection « {c} » active dans l'Outliner.")
        elif t == "MONDE":
            self.report({"INFO"}, "Voir Propriétés > Monde.")
        else:
            self.report({"INFO"}, "Voir les propriétés de la scène (Rendu, Sortie).")
        return {"FINISHED"}


def _texte(layout, texte, largeur, icone="NONE"):
    col = layout.column(align=True)
    col.scale_y = 0.85
    premiere = True
    for para in texte.split("\n"):
        for ligne in textwrap.wrap(para, largeur) or [""]:
            col.label(text=ligne, icon=icone if premiere else "BLANK1" if icone != "NONE" else "NONE")
            premiere = False


def _dessiner_journal(lay, e, largeur):
    s = journal.synthese()
    b = lay.box()
    r = b.row(align=True)
    r.prop(e, "voir_journal", text="", emboss=False,
           icon="DISCLOSURE_TRI_DOWN" if e.voir_journal else "DISCLOSURE_TRI_RIGHT")
    if not journal.actif():
        r.label(text="Journal désactivé", icon="ERROR")
    elif s is None:
        r.label(text="Journal : commence au prochain enregistrement", icon="TIME")
    else:
        r.label(text=f"Journal : {s['enregistrements']} enreg. · {len(s['jours'])} jour(s)"
                     + (" · imposé" if journal.impose() else ""),
                icon="TIME" if s["integre"] else "ERROR")
    if not e.voir_journal or s is None:
        return
    _texte(b, f"Depuis le {s['premiere']['date'][:16]} · {len(s['machines'])} machine(s) · "
              f"Blender {', '.join(s['versions'])}", largeur)
    if not s["integre"]:
        _texte(b, "Journal altéré : " + " ; ".join(s["problemes"][:3]), largeur, "ERROR")


class DIAG_PT_panneau(Panel):
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "DiagScene"
    bl_label = "DiagScene"

    def draw(self, context):
        lay = self.layout
        e = context.window_manager.diag
        largeur = max(24, int(context.region.width / (7.0 * context.preferences.view.ui_scale)))

        lay.prop(e, "config", text="")
        if e.config == PERSO:
            lay.prop(e, "config_perso", text="")
        row = lay.row()
        row.scale_y = 1.4
        row.operator("diag.analyser", icon="VIEWZOOM")

        if not e.analyse_faite:
            _texte(lay, "Lance l'analyse : l'extension lit le fichier et pointe ce qui ne va pas. "
                        "Elle ne modifie jamais rien.", largeur)
            return

        box = lay.box()
        box.label(text=f"{e.fichier} · {e.thematique}")
        total = e.nb_bloquant + e.nb_avertissement + e.nb_conseil + e.nb_acquis
        r = box.row()
        r.label(text=str(e.nb_bloquant), icon="CANCEL")
        r.label(text=str(e.nb_avertissement), icon="ERROR")
        r.label(text=str(e.nb_conseil), icon="INFO")
        r.label(text=str(e.nb_acquis), icon="CHECKMARK")
        if e.nb_bloquant + e.nb_avertissement + e.nb_conseil == 0:
            box.label(text="Aucun problème détecté", icon="CHECKMARK")
        _dessiner_journal(lay, e, largeur)
        r = lay.row(align=True)
        r.operator("diag.exporter", icon="EXPORT")
        r.operator("diag.ouvrir_dossier", text="", icon="FILE_FOLDER")
        if total == 0:
            return
        lay.row().prop(e, "filtre", expand=True)

        for a in e.alertes:
            if e.filtre != "TOUT" and a.niveau != e.filtre:
                continue
            b = lay.box()
            r = b.row(align=True)
            if a.niveau != "acquis":
                r.prop(a, "ouvert", text="", emboss=False,
                       icon="DISCLOSURE_TRI_DOWN" if a.ouvert else "DISCLOSURE_TRI_RIGHT")
            r.label(text=a.titre, icon=ICONES.get(a.niveau, "INFO"))
            if a.cible_type not in ("SCENE", ""):
                op = r.operator("diag.montrer", text="", icon="RESTRICT_SELECT_OFF")
                op.cible_type = a.cible_type
                op.cible = a.cible
            cible = rapport._cible_lisible({"cible_type": a.cible_type, "cible": a.cible})
            _texte(b, f"{cible} : {a.detail}" if cible else a.detail, largeur)
            if a.ouvert and a.niveau != "acquis":
                if a.pourquoi:
                    _texte(b, "Pourquoi : " + a.pourquoi, largeur)
                if a.comment:
                    _texte(b, "Comment corriger : " + a.comment, largeur)
                if a.lien:
                    b.operator("wm.url_open", text="En savoir plus", icon="URL").url = a.lien


class DIAG_Preferences(AddonPreferences):
    bl_idname = __package__
    journal_actif: BoolProperty(
        name="Tenir le journal de travail",
        description="À chaque enregistrement et à chaque analyse, ajouter une ligne au journal du fichier "
                    "(date, version de Blender, taille de la scène, empreinte anonyme de la machine)",
        default=True)
    dernier_toml: StringProperty(name="Dernière configuration chargée", subtype="FILE_PATH")

    def draw(self, context):
        lay = self.layout
        grille = journal.impose()
        if grille:
            lay.label(text="Tenir le journal de travail : imposé", icon="LOCKED")
            _texte(lay, f"La grille « {grille} » impose le journal : il est tenu à chaque enregistrement "
                        "et à chaque analyse, quel que soit ce réglage.", 90)
        else:
            lay.prop(self, "journal_actif")
        _texte(lay, "Le journal est écrit dans le fichier .blend (bloc de texte « .diagscene_journal »). "
                    "Il retrace l'histoire du fichier pour retrouver l'arrivée d'un problème. Il ne contient "
                    "ni votre nom d'utilisateur ni le nom de votre ordinateur.", 90)
        if not config.verrouille():
            lay.prop(self, "dernier_toml")


CLASSES = (DIAG_Preferences, DIAG_Alerte, DIAG_Etat, DIAG_OT_analyser, DIAG_OT_exporter, DIAG_OT_ouvrir_dossier,
           DIAG_OT_montrer, DIAG_PT_panneau)


def register():
    for c in CLASSES:
        bpy.utils.register_class(c)
    bpy.types.WindowManager.diag = PointerProperty(type=DIAG_Etat)


def unregister():
    del bpy.types.WindowManager.diag
    for c in reversed(CLASSES):
        bpy.utils.unregister_class(c)
