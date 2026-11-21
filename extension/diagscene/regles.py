# SPDX-License-Identifier: GPL-3.0-or-later
"""Règles de diagnostic. Aucune règle ne modifie le fichier : elles ne font que lire.

Chaque règle reçoit le contexte d'analyse et ses réglages, et renvoie des tuples
(type_de_cible, cible, détail) ou (type_de_cible, cible, détail, niveau) pour imposer un niveau.
Les textes (titre, pourquoi, comment, lien), le niveau, l'activation et les réglages
peuvent être remplacés par le fichier de configuration.
"""

import math
import os
import re

import bpy
import numpy as np
from mathutils import Vector

NIVEAUX = ("bloquant", "avertissement", "conseil", "acquis")

DEFAUTS = {}
REGLES = []


def regle(rid, titre, niveau, pourquoi, comment, actif=True, lien="", famille="", **params):
    def deco(fn):
        DEFAUTS[rid] = dict(titre=titre, niveau=niveau, actif=actif, pourquoi=pourquoi,
                            comment=comment, lien=lien, famille=famille, params=params)
        REGLES.append((rid, fn))
        return fn
    return deco


# =========================================================================
# Outils
# =========================================================================

def _n(x, decimales=1):
    """Nombre à la française (virgule décimale)."""
    return f"{x:.{decimales}f}".replace(".", ",")


def _trois(v, fmt="{:.3g}"):
    return " × ".join(fmt.format(x).replace(".", ",") for x in v)


def _liste(noms, n=8):
    noms = list(noms)
    texte = ", ".join(noms[:n])
    return texte + (f" … (+{len(noms) - n})" if len(noms) > n else "")


def _objets(scene):
    return [o for o in scene.objects if o.library is None]


def _rendus(ctx):
    """Objets maillage visibles au rendu."""
    vl = ctx["view_layer"]
    return [o for o in _objets(ctx["scene"]) if o.type == "MESH" and not o.hide_render and o.name in vl.objects]


def _materiaux_utilises(scene):
    mats = {}
    for o in _objets(scene):
        for slot in getattr(o, "material_slots", []):
            m = slot.material
            if m is None or getattr(m, "is_grease_pencil", False) or m.node_tree is None:
                continue
            entree = mats.setdefault(m.name, (m, []))
            if o not in entree[1]:
                entree[1].append(o)
    return mats


def _atteints(nt, type_sortie):
    """Noms des nodes qui contribuent réellement à la sortie active."""
    sorties = [n for n in nt.nodes if n.type == type_sortie]
    actives = [n for n in sorties if getattr(n, "is_active_output", False)] or sorties[:1]
    return _amont(actives)


def _amont(depart):
    vus, pile = set(), list(depart)
    while pile:
        n = pile.pop()
        if n.name in vus:
            continue
        vus.add(n.name)
        for entree in n.inputs:
            for lien in entree.links:
                if lien.is_valid and not lien.is_muted:
                    pile.append(lien.from_node)
    return vus


def _atteints_mat(ctx, m):
    cache = ctx.setdefault("_atteints", {})
    if m.name not in cache:
        cache[m.name] = _atteints(m.node_tree, "OUTPUT_MATERIAL")
    return cache[m.name]


def _dans_cadre_ignore(node, cadres):
    p = node.parent
    while p is not None:
        nom = (p.label or p.name).lower()
        if any(c.lower() in nom for c in cadres):
            return True
        p = p.parent
    return False


def _image_manquante(img):
    if img is None or img.type != "IMAGE" or img.packed_file is not None:
        return False
    if img.source not in ("FILE", "SEQUENCE", "MOVIE", "TILED"):
        return False
    chemin = bpy.path.abspath(img.filepath, library=img.library)
    if not chemin:
        return True
    return not os.path.exists(chemin.replace("<UDIM>", "1001"))


def _facteur_surface(o):
    s = o.matrix_world.to_scale()
    return abs(s.x * s.y * s.z) ** (2.0 / 3.0)


def _distance_camera(ctx, o):
    cam = ctx["scene"].camera
    if cam is None:
        return None
    pc = cam.matrix_world.translation
    coins = [o.matrix_world @ Vector(c) for c in o.bound_box]
    centre = sum(coins, Vector()) / 8.0
    rayon = max((c - centre).length for c in coins)
    return max(0.0, (centre - pc).length - rayon)


def _transmission(m):
    if m is None or m.node_tree is None:
        return False
    for n in m.node_tree.nodes:
        if n.type == "BSDF_PRINCIPLED":
            s = n.inputs.get("Transmission Weight") or n.inputs.get("Transmission")
            if s is not None and not s.is_linked and s.default_value > 0.5:
                return True
        if n.type in ("BSDF_GLASS", "BSDF_TRANSPARENT", "BSDF_REFRACTION"):
            return True
    return False


# --- UV : mesures (numpy) -------------------------------------------------

def _uv_couche(me):
    for c in me.uv_layers:
        if c.active_render:
            return c
    return me.uv_layers.active


def _uv_stats(ctx, o):
    cache = ctx.setdefault("_uv", {})
    if o.name in cache:
        return cache[o.name]
    me = o.data
    st = None
    couche = _uv_couche(me) if me.uv_layers else None
    P, L = len(me.polygons), len(me.loops)
    if P:
        ls = np.empty(P, dtype=np.int64); me.polygons.foreach_get("loop_start", ls)
        lt = np.empty(P, dtype=np.int64); me.polygons.foreach_get("loop_total", lt)
        a3 = np.empty(P, dtype=np.float64); me.polygons.foreach_get("area", a3)
        a3 = a3 * _facteur_surface(o)
        mi = np.empty(P, dtype=np.int64); me.polygons.foreach_get("material_index", mi)
        st = {"P": P, "ls": ls, "lt": lt, "a3": a3, "mi": mi, "uv": None}
        if couche is not None:
            uv = np.empty(L * 2, dtype=np.float64)
            attr = me.attributes.get(couche.name)
            if attr is not None and attr.domain == "CORNER":
                attr.data.foreach_get("vector", uv)
            else:
                couche.data.foreach_get("uv", uv)
            uv = uv.reshape(-1, 2)
            poly = np.repeat(np.arange(P), lt)
            nxt = np.arange(L) + 1
            nxt[ls + lt - 1] = ls
            croix = uv[:, 0] * uv[nxt, 1] - uv[nxt, 0] * uv[:, 1]
            st["uv"] = uv
            st["auv"] = np.abs(np.bincount(poly, weights=croix, minlength=P)) / 2.0
    cache[o.name] = st
    return st


def _uv_chevauchement(st, res=256, max_triangles=80000):
    """Part de la surface UV couverte deux fois ou plus (approximation sur une grille)."""
    uv, ls, lt = st["uv"], st["ls"], st["lt"]
    nb = int(np.maximum(lt - 2, 0).sum())
    if nb == 0 or nb > max_triangles:
        return None
    a = np.repeat(ls, np.maximum(lt - 2, 0))
    k = np.concatenate([np.arange(1, t - 1) for t in lt if t >= 3]) if nb else np.array([], dtype=np.int64)
    tri = np.stack([uv[a], uv[a + k], uv[a + k + 1]], axis=1) * res
    grille = np.zeros((res, res), dtype=np.uint16)
    for t in tri:
        x0, y0 = np.floor(t.min(axis=0)).astype(int)
        x1, y1 = np.ceil(t.max(axis=0)).astype(int)
        x0, y0 = max(x0, 0), max(y0, 0)
        x1, y1 = min(x1, res), min(y1, res)
        if x1 <= x0 or y1 <= y0:
            continue
        xs, ys = np.meshgrid(np.arange(x0, x1) + 0.5, np.arange(y0, y1) + 0.5)
        (ax, ay), (bx, by), (cx, cy) = t
        d = (bx - ax) * (cy - ay) - (by - ay) * (cx - ax)
        if abs(d) < 1e-12:
            continue
        w0 = ((bx - xs) * (cy - ys) - (by - ys) * (cx - xs)) / d
        w1 = ((cx - xs) * (ay - ys) - (cy - ys) * (ax - xs)) / d
        dedans = (w0 >= 0) & (w1 >= 0) & (w0 + w1 <= 1)
        grille[y0:y1, x0:x1] += dedans.astype(np.uint16)
    couverte = int((grille > 0).sum())
    if couverte == 0:
        return 0.0
    return float((grille > 1).sum()) / couverte


# =========================================================================
# Rendu
# =========================================================================

@regle("echantillons", "Échantillons de rendu excessifs", "bloquant",
       "Trop d'échantillons sans échantillonnage adaptatif rendent le rendu interminable, "
       "sans gain visible au-delà d'un certain seuil.",
       "Propriétés > Rendu > Échantillonnage : activer le seuil de bruit (échantillonnage adaptatif) "
       "et limiter le nombre d'échantillons ; le débruitage fait le reste.",
       famille="Rendu", max_cycles=4096, exiger_adaptatif=True, max_eevee=256)
def r_echantillons(ctx, p):
    sc = ctx["scene"]
    moteur = sc.render.engine
    if moteur == "CYCLES":
        c = sc.cycles
        if c.samples > p["max_cycles"]:
            yield ("SCENE", "Rendu", f"{c.samples} échantillons Cycles (plafond de la thématique : {p['max_cycles']})")
        if p["exiger_adaptatif"] and not c.use_adaptive_sampling:
            yield ("SCENE", "Rendu", "échantillonnage adaptatif (seuil de bruit) désactivé")
    elif "EEVEE" in moteur:
        s = getattr(sc.eevee, "taa_render_samples", 0)
        if s > p["max_eevee"]:
            yield ("SCENE", "Rendu", f"{s} échantillons EEVEE (plafond de la thématique : {p['max_eevee']})")


@regle("resolution", "Rendu à une fraction de la résolution", "conseil",
       "Le pourcentage de résolution réduit la taille de l'image finale : pratique pour les essais, "
       "mais l'image rendue ne sera pas à la taille prévue.",
       "Propriétés > Sortie > Format : remettre le pourcentage à 100 % pour le rendu final.",
       famille="Rendu")
def r_resolution(ctx, p):
    r = ctx["scene"].render
    if r.resolution_percentage != 100:
        x = r.resolution_x * r.resolution_percentage // 100
        y = r.resolution_y * r.resolution_percentage // 100
        yield ("SCENE", "Sortie", f"{r.resolution_percentage} % : l'image sortira en {x} × {y} px "
                                  f"au lieu de {r.resolution_x} × {r.resolution_y}")


# =========================================================================
# Caméras
# =========================================================================

def _image_de_fond(cam_obj):
    return [b for b in cam_obj.data.background_images if b.source == "IMAGE" and b.image is not None]


@regle("camera_active", "Aucune caméra active", "bloquant",
       "Sans caméra active, Blender ne peut pas lancer de rendu.",
       "Sélectionner la caméra voulue puis Ctrl+Pavé numérique 0, ou Propriétés > Scène > Caméra.",
       famille="Caméras")
def r_camera_active(ctx, p):
    if ctx["scene"].camera is None:
        yield ("SCENE", "Caméra", "la scène n'a pas de caméra active")


@regle("camera_aplomb", "Caméra pas d'aplomb", "avertissement",
       "En architecture, une caméra inclinée fait fuir les verticales : murs et poteaux semblent tomber. "
       "Un horizon penché déséquilibre l'image.",
       "Remettre la rotation X de la caméra à 90° (visée horizontale) et cadrer en hauteur avec le décalage "
       "(Propriétés de la caméra > Objectif > Décalage Y). Remettre la rotation Y à 0° pour l'horizon.",
       famille="Caméras", tolerance_deg=0.5, plongee_volontaire_deg=30.0, seulement_active=False,
       ignorer_si_image_de_fond=True)
def r_camera_aplomb(ctx, p):
    sc = ctx["scene"]
    cams = ([sc.camera] if sc.camera else []) if p["seulement_active"] else [o for o in _objets(sc) if o.type == "CAMERA"]
    for o in cams:
        if o.data.type != "PERSP":
            continue
        if p["ignorer_si_image_de_fond"] and _image_de_fond(o):
            continue  # caméra calée sur une photo (fSpy) : l'inclinaison est voulue
        m = o.matrix_world.to_3x3().normalized()
        visee, droite = -m.col[2], m.col[0]
        tangage = math.degrees(math.asin(max(-1.0, min(1.0, visee.z))))
        roulis = math.degrees(math.asin(max(-1.0, min(1.0, droite.z))))
        if p["tolerance_deg"] < abs(tangage) <= p["plongee_volontaire_deg"]:
            sens = "vers le haut" if tangage > 0 else "vers le bas"
            yield ("OBJET", o.name, f"inclinée de {_n(abs(tangage))}° {sens} : les verticales fuient "
                                    f"(décalage Y actuel : {_n(o.data.shift_y, 2)})")
        if abs(roulis) > p["tolerance_deg"]:
            yield ("OBJET", o.name, f"horizon penché de {_n(roulis)}°")


@regle("camera_focale", "Focale hors plage", "avertissement",
       "Une focale très courte déforme fortement les bords de l'image ; une focale très longue écrase "
       "la profondeur et oblige à reculer hors de la pièce.",
       "Propriétés de la caméra > Objectif : revenir dans la plage de la thématique. Pour un intérieur, "
       "préférer 18 à 35 mm et reculer la caméra plutôt que d'élargir sans fin.",
       famille="Caméras", focale_min=14.0, focale_max=135.0)
def r_camera_focale(ctx, p):
    for o in _objets(ctx["scene"]):
        if o.type == "CAMERA" and o.data.type == "PERSP" and not (p["focale_min"] <= o.data.lens <= p["focale_max"]):
            yield ("OBJET", o.name, f"focale de {_n(o.data.lens)} mm (plage : {_n(p['focale_min'], 0)} à {_n(p['focale_max'], 0)} mm)")


@regle("camera_echelle", "Caméra mise à l'échelle", "avertissement",
       "Une caméra (ou son parent) à une échelle différente de 1 fausse le cadrage, la profondeur de champ "
       "et les distances de découpe.",
       "Remettre l'échelle de la caméra à 1 (Alt+S) ; si elle est parentée à un objet mis à l'échelle, "
       "la libérer en conservant la transformation (Alt+P).",
       famille="Caméras")
def r_camera_echelle(ctx, p):
    for o in _objets(ctx["scene"]):
        if o.type == "CAMERA":
            s = o.matrix_world.to_scale()
            if any(abs(v - 1.0) > 1e-3 for v in s):
                yield ("OBJET", o.name, f"échelle {_trois(s)}" + (f" (héritée de « {o.parent.name} »)" if o.parent else ""))


@regle("camera_format", "Format du rendu désaccordé de la photo", "avertissement",
       "La caméra est calée sur une photo (fSpy), mais le rendu n'a plus les mêmes proportions : "
       "la perspective calculée ne correspond plus à l'image.",
       "Propriétés > Sortie : régler la résolution sur celle de la photo (ou dans les mêmes proportions). "
       "Ne pas recadrer en changeant la résolution après le calage.",
       famille="Caméras", tolerance=0.01)
def r_camera_format(ctx, p):
    r = ctx["scene"].render
    rendu = (r.resolution_x * r.pixel_aspect_x) / max(1e-6, r.resolution_y * r.pixel_aspect_y)
    for o in _objets(ctx["scene"]):
        if o.type != "CAMERA":
            continue
        for b in _image_de_fond(o):
            if _image_manquante(b.image):
                yield ("OBJET", o.name, f"photo de référence « {b.image.name} » introuvable", "bloquant")
                continue
            w, h = b.image.size[:]
            if w and h and abs(rendu - w / h) / (w / h) > p["tolerance"]:
                yield ("OBJET", o.name, f"rendu {r.resolution_x} × {r.resolution_y} contre photo {w} × {h} "
                                        f"(proportions {_n(rendu, 3)} au lieu de {_n(w / h, 3)})")


@regle("camera_decoupe", "Distance de découpe de la caméra", "avertissement",
       "Tout ce qui est au-delà de la distance de fin (ou en deçà du début) est coupé net au rendu.",
       "Propriétés de la caméra > Objectif > Découpe : augmenter la fin, ou réduire le début.",
       famille="Caméras", debut_max=0.5)
def r_camera_decoupe(ctx, p):
    cam = ctx["scene"].camera
    if cam is None:
        return
    pc, fin = cam.matrix_world.translation, cam.data.clip_end
    coupes = [o.name for o in _rendus(ctx) if max((o.matrix_world @ Vector(c) - pc).length for c in o.bound_box) > fin]
    if coupes:
        yield ("OBJETS", "\t".join(coupes), f"au-delà de {_n(fin)} m : {_liste(coupes)}")
    if cam.data.clip_start > p["debut_max"]:
        yield ("OBJET", cam.name, f"début de découpe à {_n(cam.data.clip_start, 2)} m : les objets proches disparaissent", "conseil")


@regle("camera_decalage", "Décalage de caméra excessif", "conseil",
       "Un décalage (shift) très fort étire l'image vers les bords et trahit souvent une caméra mal placée.",
       "Rapprocher le centre de la caméra de la zone cadrée, puis réduire le décalage.",
       famille="Caméras", max_decalage=0.5)
def r_camera_decalage(ctx, p):
    for o in _objets(ctx["scene"]):
        if o.type == "CAMERA" and max(abs(o.data.shift_x), abs(o.data.shift_y)) > p["max_decalage"]:
            yield ("OBJET", o.name, f"décalage X {_n(o.data.shift_x, 2)}, Y {_n(o.data.shift_y, 2)}")


# =========================================================================
# Lumière
# =========================================================================

@regle("lampe_noyee", "Lampe enfermée dans un objet", "bloquant",
       "La lampe est à l'intérieur d'un volume fermé (plafond, mur, luminaire opaque) : sa lumière ne sort pas.",
       "Sortir la lampe du volume (quelques millimètres sous le plafond suffisent), ou ouvrir le luminaire. "
       "Vue de dessus et de côté pour vérifier.",
       famille="Lumière")
def r_lampe_noyee(ctx, p):
    sc, dg = ctx["scene"], ctx["depsgraph"]
    dirs = [Vector(d) for d in ((1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1))]
    for o in _objets(sc):
        if o.type != "LIGHT" or o.data.type == "SUN" or o.hide_render:
            continue
        origine = o.matrix_world.translation
        dos = 0
        for d in dirs:
            ok, loc, normale, idx, obj, mat = sc.ray_cast(dg, origine, d, distance=1.0e4)
            if not ok:
                continue
            orig = obj.original if obj is not None else None
            if orig is not None and any(_transmission(s.material) for s in orig.material_slots):
                continue  # ampoule ou vitrage : la lumière passe
            if normale.dot(d) > 0:
                dos += 1
        if dos >= 5:
            yield ("OBJET", o.name, f"rayons bloqués par l'intérieur d'un volume dans {dos} directions sur 6")


@regle("puissance", "Puissance de lumière hors plage", "avertissement",
       "Une puissance démesurée oblige à baisser l'exposition et donne des contrastes brûlés ; "
       "elle trahit souvent une erreur d'échelle ou une lampe noyée qu'on a « poussée ».",
       "Revenir dans des valeurs réalistes (une ampoule d'intérieur : quelques dizaines de watts Blender ; "
       "soleil : 1 à 5 ; HDRI : force proche de 1) et régler l'exposition après.",
       famille="Lumière", point_max=2000.0, spot_max=5000.0, zone_max=2000.0, soleil_max=10.0, monde_max=3.0)
def r_puissance(ctx, p):
    maxi = {"POINT": p["point_max"], "SPOT": p["spot_max"], "AREA": p["zone_max"], "SUN": p["soleil_max"]}
    unite = {"SUN": ""}
    for o in _objets(ctx["scene"]):
        if o.type == "LIGHT" and o.data.energy > maxi.get(o.data.type, 1e12):
            yield ("OBJET", o.name, f"{_n(o.data.energy)} {unite.get(o.data.type, 'W')} (maximum : {_n(maxi[o.data.type], 0)})".rstrip())
    w = ctx["scene"].world
    if w is not None and w.node_tree is not None:
        atteints = _atteints(w.node_tree, "OUTPUT_WORLD")
        for n in w.node_tree.nodes:
            if n.type == "BACKGROUND" and n.name in atteints:
                f = n.inputs["Strength"]
                if not f.is_linked and f.default_value > p["monde_max"]:
                    yield ("MONDE", w.name, f"force du monde à {_n(f.default_value)} (maximum : {_n(p['monde_max'])})")


@regle("exposition", "Exposition qui compense", "conseil",
       "Une exposition éloignée de 0 rattrape souvent un éclairage mal dosé au lieu de le corriger.",
       "Remettre l'exposition à 0 (Propriétés > Rendu > Gestion des couleurs) et ajuster la puissance des lampes.",
       famille="Lumière", ecart_max=1.5)
def r_exposition(ctx, p):
    e = ctx["scene"].view_settings.exposure
    if abs(e) > p["ecart_max"]:
        yield ("SCENE", "Gestion des couleurs", f"exposition à {_n(e, 2)}")


@regle("ombres_dures", "Lumière ponctuelle sans taille", "conseil",
       "Une lampe de rayon nul donne des ombres parfaitement nettes, qui n'existent pas dans la réalité.",
       "Propriétés de la lampe : donner un rayon réaliste (quelques centimètres pour une ampoule, "
       "davantage pour un luminaire diffusant).",
       famille="Lumière", rayon_min=0.01)
def r_ombres_dures(ctx, p):
    for o in _objets(ctx["scene"]):
        if o.type == "LIGHT" and o.data.type in ("POINT", "SPOT") and o.data.shadow_soft_size < p["rayon_min"]:
            yield ("OBJET", o.name, f"rayon de {_n(o.data.shadow_soft_size * 100)} cm")


@regle("ombres_desactivees", "Lampe sans ombre", "conseil",
       "Une lampe qui ne projette pas d'ombre éclaire à travers les murs et aplatit l'image.",
       "Propriétés de la lampe : cocher Ombre, sauf effet voulu.",
       famille="Lumière")
def r_ombres_desactivees(ctx, p):
    for o in _objets(ctx["scene"]):
        if o.type == "LIGHT" and not getattr(o.data, "use_shadow", True):
            yield ("OBJET", o.name, "ombre désactivée")


@regle("lampes_non_liees", "Lampes identiques non liées", "conseil",
       "Des lampes copiées avec Maj+D ont chacune leurs réglages : pour changer la puissance de toutes, "
       "il faut les reprendre une par une, et elles finissent par différer.",
       "Dupliquer avec Alt+D (copie liée), ou les relier après coup : sélectionner les lampes puis la lampe modèle, "
       "Ctrl+L > Lier les données.",
       famille="Lumière", groupe_min=3)
def r_lampes_non_liees(ctx, p):
    groupes = {}
    for o in _objets(ctx["scene"]):
        if o.type == "LIGHT":
            d = o.data
            cle = (d.type, round(d.energy, 3), tuple(round(c, 3) for c in d.color),
                   round(d.shadow_soft_size, 3), round(getattr(d, "spot_size", 0), 3), round(getattr(d, "size", 0), 3))
            groupes.setdefault(cle, []).append(o)
    for objs in groupes.values():
        donnees = {o.data.name for o in objs}
        if len(objs) >= p["groupe_min"] and len(donnees) == len(objs):
            noms = [o.name for o in objs]
            yield ("OBJETS", "\t".join(noms), f"{len(objs)} lampes aux réglages identiques mais indépendantes : {_liste(noms)}")


@regle("couleur_sans_temperature", "Couleur de lampe sans température", "conseil",
       "Une teinte réglée à la main est difficile à reproduire et souvent irréaliste ; la température en kelvins "
       "correspond aux sources réelles (2700 K chaud, 4000 K neutre, 6500 K lumière du jour).",
       "Propriétés de la lampe : remettre la couleur en blanc et utiliser la température.",
       famille="Lumière", blanc_min=0.98)
def r_couleur_temp(ctx, p):
    par_donnee = {}
    for o in _objets(ctx["scene"]):
        if o.type == "LIGHT" and hasattr(o.data, "use_temperature") and not o.data.use_temperature \
                and min(o.data.color) < p["blanc_min"]:
            par_donnee.setdefault(o.data.name, (o.data, []))[1].append(o.name)
    for d, noms in par_donnee.values():
        yield ("OBJETS", "\t".join(noms), f"couleur RVB {_trois(d.color, '{:.2f}')} sur {len(noms)} lampe(s) : {_liste(noms)}")


@regle("hdri_resolution", "Image d'environnement trop petite", "conseil",
       "Une HDRI de faible résolution suffit pour l'éclairage, mais elle paraît floue dans les reflets "
       "et en arrière-plan.",
       "Utiliser une version 2k ou 4k de la même HDRI (Poly Haven les propose toutes), "
       "ou masquer l'arrière-plan si seul l'éclairage compte.",
       famille="Lumière", largeur_min=2048)
def r_hdri(ctx, p):
    w = ctx["scene"].world
    if w is None or w.node_tree is None:
        return
    atteints = _atteints(w.node_tree, "OUTPUT_WORLD")
    for n in w.node_tree.nodes:
        if n.type == "TEX_ENVIRONMENT" and n.name in atteints and n.image is not None:
            larg, haut = n.image.size[:]
            if 0 < larg < p["largeur_min"]:
                yield ("MONDE", w.name, f"« {n.image.name} » : {larg} × {haut} px (minimum conseillé : {p['largeur_min']})")


# =========================================================================
# Objets
# =========================================================================

def _coord_objet(ctx, o):
    """Le matériau de l'objet dépend-il de ses coordonnées Objet ou Générées ?"""
    for s in o.material_slots:
        m = s.material
        if m is None or m.node_tree is None:
            continue
        att = _atteints_mat(ctx, m)
        for n in m.node_tree.nodes:
            if n.name not in att:
                continue
            if n.type == "TEX_COORD" and (n.outputs["Object"].is_linked or n.outputs["Generated"].is_linked):
                return True
            if n.type.startswith("TEX_") and n.type not in ("TEX_IMAGE", "TEX_COORD", "TEX_ENVIRONMENT") \
                    and "Vector" in n.inputs and not n.inputs["Vector"].is_linked:
                return True
            if n.type == "TEX_IMAGE" and n.projection == "BOX" and not n.inputs["Vector"].is_linked:
                return True
    return False


@regle("echelle_non_appliquee", "Échelle non appliquée", "avertissement",
       "L'objet a l'air juste à l'écran, mais Blender garde un multiplicateur caché : les chanfreins, "
       "les épaisseurs, les réseaux et les textures sont calculés sur l'objet d'origine, puis étirés.",
       "Sélectionner l'objet, Ctrl+A > Échelle. Si le maillage est partagé avec d'autres objets, "
       "le rendre unique d'abord (Objet > Relations > Rendre unique).",
       famille="Objets", types=["MESH", "CURVE", "SURFACE", "FONT", "META"],
       modificateurs_sensibles=["BEVEL", "SOLIDIFY", "ARRAY", "SUBSURF", "WIREFRAME", "SCREW", "DISPLACE"],
       niveau_si_sensible="bloquant")
def r_echelle(ctx, p):
    for o in _objets(ctx["scene"]):
        s = o.scale
        if o.type not in p["types"] or min(s) < 0 or all(abs(v - 1.0) <= 1e-4 for v in s):
            continue
        detail = f"échelle {_trois(s)}, dimensions réelles {_trois(o.dimensions, '{:.3g}')} m"
        mods = [m.name for m in o.modifiers if m.type in p["modificateurs_sensibles"]]
        if mods:
            yield ("OBJET", o.name, detail + f" ; modificateur(s) faussé(s) : {', '.join(mods)}", p["niveau_si_sensible"])
        elif _coord_objet(ctx, o):
            yield ("OBJET", o.name, detail + " ; texture placée en coordonnées Objet ou Générées : motif étiré", p["niveau_si_sensible"])
        else:
            yield ("OBJET", o.name, detail)


@regle("echelle_negative", "Échelle négative (objet en miroir)", "avertissement",
       "Une échelle négative retourne l'objet en miroir : les normales peuvent s'inverser, ce qui perturbe "
       "les matériaux, les modificateurs et l'export.",
       "Ctrl+A > Échelle, puis vérifier l'orientation des faces (Superpositions > Orientation des faces) "
       "et recalculer les normales si besoin (Édition, Maj+N).",
       famille="Objets")
def r_echelle_negative(ctx, p):
    for o in _objets(ctx["scene"]):
        if o.type in ("MESH", "CURVE", "SURFACE", "FONT") and min(o.scale) < 0:
            yield ("OBJET", o.name, f"échelle {_trois(o.scale)}")


@regle("visibilite", "Visibilité différente à l'écran et au rendu", "conseil",
       "Un objet visible à l'écran mais exclu du rendu, ou l'inverse, réserve de mauvaises surprises "
       "au moment du rendu.",
       "Vérifier les icônes œil et appareil photo dans l'Outliner (filtre : afficher l'icône de rendu).",
       famille="Objets")
def r_visibilite(ctx, p):
    vl = ctx["view_layer"]
    ecran_seul, rendu_seul = [], []
    for o in _objets(ctx["scene"]):
        if o.type in ("EMPTY", "CAMERA") or o.name not in vl.objects:
            continue
        ecran = not o.hide_get(view_layer=vl) and not o.hide_viewport
        rendu = not o.hide_render
        if ecran and not rendu:
            ecran_seul.append(o.name)
        elif rendu and not ecran:
            rendu_seul.append(o.name)
    if ecran_seul:
        yield ("OBJETS", "\t".join(ecran_seul), f"visibles à l'écran mais exclus du rendu : {_liste(ecran_seul)}")
    if rendu_seul:
        yield ("OBJETS", "\t".join(rendu_seul), f"masqués à l'écran mais présents au rendu : {_liste(rendu_seul)}")


# =========================================================================
# Matériaux
# =========================================================================

@regle("materiau_absent", "Objet sans matériau", "conseil",
       "Un objet sans matériau, ou avec un emplacement vide, est rendu en gris par défaut.",
       "Propriétés > Matériau : attribuer un matériau à chaque emplacement, ou supprimer les emplacements vides.",
       famille="Matériaux", types=["MESH", "CURVE", "FONT"])
def r_materiau_absent(ctx, p):
    sans, vides = [], []
    for o in _objets(ctx["scene"]):
        if o.type not in p["types"]:
            continue
        if not o.material_slots:
            sans.append(o.name)
        elif any(s.material is None for s in o.material_slots):
            vides.append(o.name)
    if sans:
        yield ("OBJETS", "\t".join(sans), f"sans matériau : {_liste(sans)}")
    if vides:
        yield ("OBJETS", "\t".join(vides), f"avec un emplacement de matériau vide : {_liste(vides)}")


@regle("nodes_non_relies", "Nodes non reliés", "conseil",
       "Des nodes qui ne mènent pas à la sortie du matériau n'ont aucun effet : ils encombrent l'arbre "
       "et rendent le matériau difficile à relire.",
       "Supprimer les nodes inutiles, ou les ranger dans un cadre nommé « démo » ou « OLD » "
       "s'ils doivent rester pour mémoire.",
       famille="Matériaux", cadres_ignores=["demo", "démo", "old", "brouillon"])
def r_nodes(ctx, p):
    for nom, (m, objs) in ctx["mats"].items():
        att = _atteints_mat(ctx, m)
        orph = [n for n in m.node_tree.nodes
                if n.name not in att and n.type not in ("FRAME", "REROUTE", "OUTPUT_MATERIAL")
                and not _dans_cadre_ignore(n, p["cadres_ignores"])]
        if orph:
            yield ("MATERIAU", m.name, f"{len(orph)} node(s) sans effet : {_liste([n.label or n.name for n in orph], 6)}")


@regle("textures_manquantes", "Texture introuvable", "bloquant",
       "L'image n'est plus à l'emplacement enregistré : elle apparaîtra en rose au rendu, "
       "et le fichier est inutilisable sur un autre ordinateur.",
       "Fichier > Données externes > Rechercher les fichiers manquants, puis Empaqueter les ressources "
       "(ou ranger les textures dans un dossier textures/ à côté du fichier, en chemins relatifs).",
       famille="Matériaux")
def r_textures(ctx, p):
    for nom, (m, objs) in ctx["mats"].items():
        att = _atteints_mat(ctx, m)
        for n in m.node_tree.nodes:
            if n.type != "TEX_IMAGE" or n.name not in att:
                continue
            if n.image is None:
                yield ("NODE", f"{m.name}\t{n.name}", f"node « {n.label or n.name} » sans image")
            elif _image_manquante(n.image):
                yield ("NODE", f"{m.name}\t{n.name}", f"« {n.image.name} » introuvable ({n.image.filepath})")
    w = ctx["scene"].world
    if w is not None and w.node_tree is not None:
        att = _atteints(w.node_tree, "OUTPUT_WORLD")
        for n in w.node_tree.nodes:
            if n.type == "TEX_ENVIRONMENT" and n.name in att and _image_manquante(n.image):
                yield ("MONDE", w.name, f"« {n.image.name} » introuvable ({n.image.filepath})")


_PRINCIPLED_DONNEES = {"Roughness", "Metallic", "Alpha", "Specular IOR Level", "Transmission Weight",
                       "Coat Weight", "Coat Roughness", "Sheen Weight", "Subsurface Weight", "Anisotropic"}


@regle("espace_couleur", "Espace colorimétrique incorrect", "avertissement",
       "Les cartes de données (rugosité, métal, normale, hauteur) doivent être lues telles quelles. "
       "En sRGB, Blender les corrige comme des couleurs et les valeurs sont faussées ; à l'inverse, "
       "une image de couleur en Non-Color paraît délavée.",
       "Dans le node Image Texture, régler l'espace colorimétrique sur Non-Color pour les cartes de données, "
       "et sur sRGB pour la couleur de base.",
       famille="Matériaux")
def r_espace(ctx, p):
    for nom, (m, objs) in ctx["mats"].items():
        att = _atteints_mat(ctx, m)
        for n in m.node_tree.nodes:
            if n.type != "TEX_IMAGE" or n.image is None or n.name not in att:
                continue
            donnees = n.image.colorspace_settings.is_data
            for lien in n.outputs["Color"].links:
                vers, sock = lien.to_node, lien.to_socket.identifier
                attend_donnees = ((vers.type == "NORMAL_MAP" and sock == "Color")
                                  or (vers.type in ("BUMP", "DISPLACEMENT") and sock == "Height")
                                  or (vers.type == "BSDF_PRINCIPLED" and sock in _PRINCIPLED_DONNEES))
                attend_couleur = vers.type == "BSDF_PRINCIPLED" and sock == "Base Color"
                if attend_donnees and not donnees:
                    yield ("NODE", f"{m.name}\t{n.name}",
                           f"« {n.image.name} » en {n.image.colorspace_settings.name} vers {lien.to_socket.name} : doit être en Non-Color")
                elif attend_couleur and donnees:
                    yield ("NODE", f"{m.name}\t{n.name}",
                           f"« {n.image.name} » en {n.image.colorspace_settings.name} vers la couleur de base : doit être en sRGB")


def _methode_deplacement(m):
    return getattr(m, "displacement_method", None) or getattr(getattr(m, "cycles", None), "displacement_method", None)


def _deplacement_relie(ctx, m):
    att = _atteints_mat(ctx, m)
    return any(n.type == "OUTPUT_MATERIAL" and n.name in att and n.inputs["Displacement"].is_linked
               for n in m.node_tree.nodes)


@regle("deplacement_adapte", "Déplacement inadapté à la distance", "conseil",
       "Un vrai déplacement déforme la surface mais coûte cher ; sur un objet éloigné de la caméra, "
       "un bump donne le même résultat, bien plus vite. À l'inverse, de très près, le bump laisse "
       "une silhouette plate.",
       "Propriétés du matériau > Paramètres > Surface > Déplacement : « Bump seul » pour les objets éloignés ; "
       "« Déplacement et bump » avec une subdivision suffisante pour les gros plans.",
       famille="Matériaux", distance_loin=8.0, distance_proche=1.0, faces_min=1000)
def r_deplacement(ctx, p):
    for nom, (m, objs) in ctx["mats"].items():
        if not _deplacement_relie(ctx, m):
            continue
        methode = _methode_deplacement(m)
        for o in objs:
            if o.type != "MESH":
                continue
            d = _distance_camera(ctx, o)
            vrai = methode in ("DISPLACEMENT", "BOTH")
            if vrai and not any(md.type in ("SUBSURF", "MULTIRES") for md in o.modifiers) and len(o.data.polygons) < p["faces_min"]:
                yield ("OBJET", o.name, f"« {m.name} » en vrai déplacement, mais l'objet n'a que {len(o.data.polygons)} faces "
                                        f"et aucune subdivision : le relief ne peut pas apparaître", "avertissement")
            elif d is not None and vrai and d > p["distance_loin"]:
                yield ("OBJET", o.name, f"« {m.name} » en vrai déplacement à {_n(d)} m de la caméra : un bump suffit")
            elif d is not None and methode == "BUMP" and d < p["distance_proche"]:
                yield ("OBJET", o.name, f"« {m.name} » en bump à {_n(d, 2)} m de la caméra : silhouette plate en gros plan")


@regle("materiau_uniforme", "Matériau uniforme sur une grande surface", "conseil",
       "Une grande surface sans aucune variation (couleur et rugosité constantes) paraît artificielle : "
       "dans la réalité, tout matériau s'use, se salit et accroche la lumière de façon irrégulière.",
       "Ajouter une variation : un Noise passé dans une Color Ramp sur la rugosité, ou un Mix entre deux teintes "
       "piloté par une texture.",
       famille="Matériaux", surface_min=2.0)
def r_uniforme(ctx, p):
    trouves = []
    for nom, (m, objs) in ctx["mats"].items():
        if _transmission(m):
            continue
        att = _atteints_mat(ctx, m)
        if any(n.type.startswith("TEX_") or n.type in ("ATTRIBUTE", "VERTEX_COLOR", "AMBIENT_OCCLUSION")
               for n in m.node_tree.nodes if n.name in att):
            continue
        if not any(n.type == "BSDF_PRINCIPLED" for n in m.node_tree.nodes if n.name in att):
            continue
        surface = 0.0
        for o in objs:
            if o.type != "MESH" or o.hide_render:
                continue
            st = _uv_stats(ctx, o)
            if st is None:
                continue
            for i, sl in enumerate(o.material_slots):
                if sl.material == m:
                    surface += float(st["a3"][st["mi"] == i].sum())
        if surface >= p["surface_min"]:
            trouves.append((surface, nom))
    if trouves:
        trouves.sort(reverse=True)
        noms = [n for _, n in trouves]
        yield ("MATERIAUX", "\t".join(noms), f"{len(noms)} matériau(x) sans aucune variation : "
               + _liste([f"{n} ({_n(a, 0)} m²)" for a, n in trouves], 6))


def _facteur_pilote(n):
    for s in n.inputs:
        if s.identifier.startswith("Fac") and s.is_linked:
            amont = _amont([l.from_node for l in s.links])
            return amont
    return None


@regle("techniques", "Techniques de matériau", "conseil",
       "Les variations procédurales (Noise, Color Ramp, Mix piloté par une texture) sont ce qui fait passer "
       "un matériau de « plastique » à crédible.",
       "Noise > Color Ramp pour contrôler le contraste ; brancher le résultat dans le facteur d'un Mix "
       "pour mélanger deux couleurs, deux rugosités ou deux matériaux.",
       famille="Matériaux", attendues=["noise", "color_ramp", "mix_pilote"])
def r_techniques(ctx, p):
    trouve = {"noise": [], "color_ramp": [], "mix_pilote": []}
    for nom, (m, objs) in ctx["mats"].items():
        att = _atteints_mat(ctx, m)
        nodes = [n for n in m.node_tree.nodes if n.name in att]
        if any(n.type == "TEX_NOISE" for n in nodes):
            trouve["noise"].append(nom)
        if any(n.type == "VALTORGB" for n in nodes):
            trouve["color_ramp"].append(nom)
        for n in nodes:
            if n.type in ("MIX", "MIX_RGB", "MIX_SHADER"):
                amont = _facteur_pilote(n)
                if amont and any(m.node_tree.nodes[a].type.startswith("TEX_") for a in amont):
                    trouve["mix_pilote"].append(nom)
                    break
    libelles = {"noise": "Texture Noise", "color_ramp": "Color Ramp", "mix_pilote": "Mix piloté par une texture"}
    for t in p["attendues"]:
        if t not in trouve:
            continue
        if trouve[t]:
            yield ("MATERIAUX", "\t".join(trouve[t]), f"{libelles[t]} : utilisé dans {_liste(trouve[t], 6)}", "acquis")
        else:
            yield ("SCENE", "", f"{libelles[t]} : utilisé dans aucun matériau")


# =========================================================================
# UV
# =========================================================================

def _objets_uv(ctx, p):
    """Objets maillage dont un matériau utilise des coordonnées UV
    (avec p["seulement_textures_image"] : seulement pour placer une texture image)."""
    test = _utilise_uv_image if p.get("seulement_textures_image") else _utilise_uv
    if p.get("seulement_selection"):
        base = [o for o in ctx["selection"] if o.type == "MESH"]
    else:
        base = _rendus(ctx)
    res = []
    for o in base:
        for s in o.material_slots:
            m = s.material
            if m is not None and m.node_tree is not None and test(m.node_tree, _atteints_mat(ctx, m)):
                res.append(o)
                break
        else:
            if p.get("seulement_selection"):
                res.append(o)
    return res


def _utilise_uv(nt, atteints):
    for n in nt.nodes:
        if n.name not in atteints:
            continue
        if n.type == "UVMAP":
            return True
        if n.type == "TEX_COORD" and n.outputs["UV"].is_linked:
            return True
        if n.type == "TEX_IMAGE" and not n.inputs["Vector"].is_linked and n.projection == "FLAT":
            return True
        if n.type == "NORMAL_MAP" and n.space == "TANGENT":
            return True
    return False


def _utilise_uv_image(nt, atteints):
    """Une texture image atteinte par la sortie est placée par des UV (projection plate)."""
    for n in nt.nodes:
        if n.name not in atteints or n.type != "TEX_IMAGE" or n.image is None or n.projection != "FLAT":
            continue
        if not n.inputs["Vector"].is_linked:
            return True
        amont = _amont([n])
        for a in nt.nodes:
            if a.name in amont and (a.type == "UVMAP" or (a.type == "TEX_COORD" and a.outputs["UV"].is_linked)):
                return True
    return False


def _groupes_uv(ctx, o, p=None):
    """(matériau, masque des faces) pour chaque matériau de l'objet qui utilise des UV."""
    st = _uv_stats(ctx, o)
    if st is None:
        return st, []
    groupes = {}
    for i, s in enumerate(o.material_slots):
        m = s.material
        test = _utilise_uv_image if (p or {}).get("seulement_textures_image") else _utilise_uv
        if m is not None and m.node_tree is not None and test(m.node_tree, _atteints_mat(ctx, m)):
            masque = st["mi"] == i
            if masque.any():
                if m.name in groupes:
                    groupes[m.name] = (m, groupes[m.name][1] | masque)
                else:
                    groupes[m.name] = (m, masque)
    return st, list(groupes.values())


@regle("uv_manquantes", "Carte UV manquante", "bloquant",
       "Le matériau place ses textures avec une carte UV, mais l'objet n'en a pas : "
       "les textures seront écrasées en une couleur unique ou mal placées.",
       "Édition, tout sélectionner, U > Dépliage (ou Projection intelligente) ; "
       "ou changer les coordonnées du matériau (Générées, Objet, projection Boîte).",
       famille="UV", seulement_textures_image=False)
def r_uv(ctx, p):
    for o in _objets_uv(ctx, {"seulement_textures_image": p["seulement_textures_image"]}):
        if not o.data.uv_layers:
            yield ("OBJET", o.name, "aucune carte UV, alors qu'un de ses matériaux en utilise une")


@regle("uv_ecrasees", "UV non dépliées", "bloquant",
       "Les faces qui portent ce matériau ont des UV d'aire nulle (ramenées sur un point ou une ligne) : "
       "la texture y apparaît comme une couleur unique ou des traînées.",
       "Édition : sélectionner les faces du matériau (Propriétés > Matériau > Sélectionner), U > Dépliage "
       "ou Projection intelligente, puis vérifier dans l'éditeur UV.",
       famille="UV", part_partielle=0.1, niveau_partiel="avertissement", seulement_textures_image=False)
def r_uv_ecrasees(ctx, p):
    for o in _objets_uv(ctx, {"seulement_textures_image": p["seulement_textures_image"]}):
        if not o.data.uv_layers:
            continue
        st, groupes = _groupes_uv(ctx, o, p)
        for m, masque in groupes:
            a3 = st["a3"][masque]
            total = a3.sum()
            if total <= 0:
                continue
            nulles = a3[st["auv"][masque] < 1e-10].sum() / total
            if nulles > 0.99:
                yield ("OBJET", o.name, f"« {m.name} » : aucune face dépliée")
            elif nulles > p["part_partielle"]:
                yield ("OBJET", o.name, f"« {m.name} » : {_n(100 * nulles, 0)} % de la surface sans UV", p["niveau_partiel"])


@regle("uv_etirement", "UV étirées", "conseil",
       "Quand la surface UV d'une face n'est pas proportionnelle à sa surface réelle, la texture y est "
       "étirée ou écrasée : briques déformées, veinage qui change de taille.",
       "Marquer des coutures (Ctrl+E > Marquer la couture) là où la forme se plie, puis redéplier. "
       "Éditeur UV > Superpositions > Étirement pour voir les zones en cause.",
       famille="UV", facteur=2.0, part_max=0.2, seulement_selection=False)
def r_uv_etirement(ctx, p):
    for o in _objets_uv(ctx, p):
        if not o.data.uv_layers:
            continue
        st, groupes = _groupes_uv(ctx, o)
        for m, masque in groupes:
            ok = masque & (st["a3"] > 1e-9) & (st["auv"] > 1e-12)
            if ok.sum() < 2:
                continue
            r = st["auv"][ok] / st["a3"][ok]
            med = np.median(r)
            hors = (r > med * p["facteur"]) | (r < med / p["facteur"])
            part = st["a3"][ok][hors].sum() / st["a3"][ok].sum()
            if part > p["part_max"]:
                yield ("OBJET", o.name, f"« {m.name} » : {_n(100 * part, 0)} % de la surface étirée ou écrasée "
                                        f"d'un facteur supérieur à {_n(p['facteur'], 1)}")


@regle("uv_densite", "Échelle de texture incohérente entre objets", "conseil",
       "Deux objets qui partagent un matériau devraient montrer la texture à la même échelle ; sinon "
       "la brique est géante sur un mur et minuscule sur l'autre.",
       "Dans l'éditeur UV, mettre les îlots à la même échelle (UV > Moyenner l'échelle des îlots, "
       "avec les objets en Édition ensemble), après avoir appliqué l'échelle des objets.",
       famille="UV", ecart_max=2.0)
def r_uv_densite(ctx, p):
    for nom, (m, objs) in ctx["mats"].items():
        if not _utilise_uv(m.node_tree, _atteints_mat(ctx, m)):
            continue
        dens = {}
        for o in objs:
            if o.type != "MESH" or not o.data.uv_layers:
                continue
            st, groupes = _groupes_uv(ctx, o)
            for mm, masque in groupes:
                if mm.name == m.name and st["uv"] is not None:
                    a3, auv = st["a3"][masque].sum(), st["auv"][masque].sum()
                    if a3 > 0 and auv > 0:
                        dens[o.name] = math.sqrt(auv / a3)
        if len(dens) >= 2:
            mini, maxi = min(dens, key=dens.get), max(dens, key=dens.get)
            if dens[maxi] / dens[mini] > p["ecart_max"]:
                yield ("MATERIAU", m.name, f"texture {_n(dens[maxi] / dens[mini])} fois plus serrée sur « {maxi} » "
                                           f"que sur « {mini} »")


@regle("uv_cartes_multiples", "Plusieurs cartes UV", "conseil",
       "La carte UV éditée (active) n'est pas celle utilisée au rendu : on déplie l'une et on voit l'autre. "
       "C'est une source classique de confusion au moment du bake.",
       "Propriétés > Données de l'objet > Cartes UV : vérifier quelle carte porte l'icône d'appareil photo (rendu) "
       "et laquelle est sélectionnée.",
       famille="UV")
def r_uv_multiples(ctx, p):
    for o in _rendus(ctx):
        cartes = o.data.uv_layers
        if len(cartes) > 1:
            rendu = _uv_couche(o.data)
            if cartes.active is not None and rendu is not None and cartes.active.name != rendu.name:
                yield ("OBJET", o.name, f"carte éditée « {cartes.active.name} », carte rendue « {rendu.name} »")


@regle("uv_chevauchement", "Îlots UV qui se chevauchent", "bloquant",
       "Pour un bake, chaque point de la surface doit avoir sa propre place dans l'image : des îlots superposés "
       "reçoivent la même zone et le résultat se mélange.",
       "Éditeur UV : Sélectionner > Chevauchements, puis séparer les îlots ; UV > Empaqueter les îlots.",
       actif=False, famille="UV", seulement_selection=False, seuil=0.02, resolution=256)
def r_uv_chevauchement(ctx, p):
    for o in _objets_uv(ctx, p):
        st = _uv_stats(ctx, o)
        if st is None or st["uv"] is None:
            continue
        part = _uv_chevauchement(st, res=p["resolution"])
        if part is not None and part > p["seuil"]:
            yield ("OBJET", o.name, f"environ {_n(100 * part, 0)} % de la surface UV couverte plusieurs fois")


@regle("uv_hors_cadre", "UV hors du carré 0–1", "bloquant",
       "Pour un bake, tout ce qui sort du carré de l'image est perdu ou se répète.",
       "Éditeur UV : UV > Empaqueter les îlots, ou ramener les îlots dans le carré.",
       actif=False, famille="UV", seulement_selection=False, tolerance=0.001)
def r_uv_hors_cadre(ctx, p):
    for o in _objets_uv(ctx, p):
        st = _uv_stats(ctx, o)
        if st is None or st["uv"] is None:
            continue
        t = p["tolerance"]
        hors = ((st["uv"] < -t) | (st["uv"] > 1 + t)).any(axis=1).mean()
        if hors > 0:
            yield ("OBJET", o.name, f"{_n(100 * hors, 0)} % des sommets UV hors du cadre")


# =========================================================================
# Bake (règles inactives par défaut : thématique « Vérification avant le bake »)
# =========================================================================

_TYPES_DONNEES = {"NORMAL", "ROUGHNESS", "AO", "POSITION", "UV", "EMIT_STRENGTH"}


@regle("bake_selection", "Rien à baker", "bloquant",
       "Le bake s'applique aux objets sélectionnés : sans sélection, il n'y a rien à calculer.",
       "Sélectionner l'objet (ou les objets) dont on veut calculer la texture.",
       actif=False, famille="Bake")
def r_bake_selection(ctx, p):
    if not [o for o in ctx["selection"] if o.type == "MESH"]:
        yield ("SCENE", "", "aucun objet maillage sélectionné")


@regle("bake_moteur", "Moteur incompatible avec le bake", "bloquant",
       "Le bake n'est disponible qu'avec Cycles.",
       "Propriétés > Rendu > Moteur de rendu : Cycles.",
       actif=False, famille="Bake")
def r_bake_moteur(ctx, p):
    if ctx["scene"].render.engine != "CYCLES":
        yield ("SCENE", "Rendu", f"moteur actuel : {ctx['scene'].render.engine}")


@regle("bake_image_cible", "Pas d'image cible pour le bake", "bloquant",
       "Cycles écrit le bake dans le node Image Texture sélectionné (actif) de chaque matériau de l'objet. "
       "Sans lui : erreur « No active image found ».",
       "Dans chaque matériau de l'objet : ajouter un node Image Texture (non relié), lui créer une image (Nouveau), "
       "puis cliquer dessus pour qu'il soit le node actif, juste avant de lancer le bake.",
       actif=False, famille="Bake")
def r_bake_cible(ctx, p):
    if ctx["scene"].render.bake.target != "IMAGE_TEXTURES":
        return
    for o in ctx["selection"]:
        if o.type != "MESH":
            continue
        if not o.material_slots or all(s.material is None for s in o.material_slots):
            yield ("OBJET", o.name, "aucun matériau : il faut un matériau pour porter l'image cible")
            continue
        for s in o.material_slots:
            m = s.material
            if m is None or m.node_tree is None:
                continue
            actif = m.node_tree.nodes.active
            if actif is None or actif.type != "TEX_IMAGE":
                yield ("MATERIAU", m.name, f"objet « {o.name} » : le node actif est « {actif.name if actif else 'aucun'} », "
                                           f"pas un node Image Texture")
            elif actif.image is None:
                yield ("NODE", f"{m.name}\t{actif.name}", f"objet « {o.name} » : le node actif n'a pas d'image")


def _images_cibles(ctx):
    vues = {}
    for o in ctx["selection"]:
        for s in getattr(o, "material_slots", []):
            m = s.material
            if m is not None and m.node_tree is not None:
                a = m.node_tree.nodes.active
                if a is not None and a.type == "TEX_IMAGE" and a.image is not None:
                    vues[a.image.name] = (a.image, m, a)
    return vues.values()


@regle("bake_image_enregistree", "Image du bake non enregistrée", "bloquant",
       "L'image calculée n'existe que dans la mémoire de Blender : elle est perdue à la fermeture du fichier. "
       "C'est l'erreur la plus fréquente du bake.",
       "Après le bake : éditeur d'images, Image > Enregistrer sous (PNG, ou EXR pour les normales en 32 bits). "
       "Ou bien Fichier > Données externes > Empaqueter les ressources.",
       actif=False, famille="Bake")
def r_bake_enregistree(ctx, p):
    for img, m, n in _images_cibles(ctx):
        if img.is_dirty:
            yield ("NODE", f"{m.name}\t{n.name}", f"« {img.name} » modifiée et non enregistrée")
        elif img.source == "GENERATED" and img.packed_file is None:
            yield ("NODE", f"{m.name}\t{n.name}", f"« {img.name} » créée dans Blender : à enregistrer juste après le bake",
                   "avertissement")


@regle("bake_espace_couleur", "Espace colorimétrique de l'image cible", "avertissement",
       "Une carte de données (normales, rugosité, occlusion) calculée dans une image sRGB est faussée ; "
       "une couleur calculée dans une image Non-Color paraît délavée.",
       "Éditeur d'images, panneau Image : Non-Color pour les cartes de données, sRGB pour Diffuse et Combined.",
       actif=False, famille="Bake")
def r_bake_espace(ctx, p):
    t = ctx["scene"].cycles.bake_type
    for img, m, n in _images_cibles(ctx):
        donnees = img.colorspace_settings.is_data
        if t in _TYPES_DONNEES and not donnees:
            yield ("NODE", f"{m.name}\t{n.name}", f"bake {t} dans « {img.name} » en {img.colorspace_settings.name} : choisir Non-Color")
        elif t in ("DIFFUSE", "COMBINED", "GLOSSY", "TRANSMISSION", "EMIT") and donnees:
            yield ("NODE", f"{m.name}\t{n.name}", f"bake {t} dans « {img.name} » en Non-Color : choisir sRGB")


@regle("bake_marge", "Marge du bake trop faible", "avertissement",
       "La marge prolonge la texture au-delà des îlots ; trop faible, des coutures sombres apparaissent "
       "aux bords des îlots, surtout quand la texture est réduite.",
       "Propriétés > Rendu > Bake > Sortie > Marge : environ 16 px pour une image 2k.",
       actif=False, famille="Bake", px_par_1024=8)
def r_bake_marge(ctx, p):
    marge = ctx["scene"].render.bake.margin
    for img, m, n in _images_cibles(ctx):
        mini = max(4, int(p["px_par_1024"] * max(img.size[:]) / 1024))
        if marge < mini:
            yield ("SCENE", "Bake", f"marge de {marge} px pour « {img.name} » ({img.size[0]} px) : au moins {mini} px")


@regle("bake_eclairage", "Éclairage cuit dans la texture", "conseil",
       "En bake Diffuse, les contributions Directe et Indirecte inscrivent l'éclairage de la scène dans la texture : "
       "utile pour un objet figé, gênant pour une texture de couleur réutilisable.",
       "Propriétés > Rendu > Bake > Influence : ne garder que Couleur pour obtenir la seule couleur du matériau.",
       actif=False, famille="Bake")
def r_bake_eclairage(ctx, p):
    b = ctx["scene"].render.bake
    if ctx["scene"].cycles.bake_type == "DIFFUSE" and (b.use_pass_direct or b.use_pass_indirect):
        yield ("SCENE", "Bake", "bake Diffuse avec Direct ou Indirect coché")


@regle("bake_cage", "Extrusion nulle (sélection vers active)", "avertissement",
       "En « sélection vers active », les rayons partent de l'objet actif ; sans extrusion ni cage, "
       "ils manquent l'objet détaillé et laissent des taches.",
       "Propriétés > Rendu > Bake > Sélection vers active : donner une extrusion (quelques millimètres) ou une cage.",
       actif=False, famille="Bake")
def r_bake_cage(ctx, p):
    b = ctx["scene"].render.bake
    if b.use_selected_to_active and not b.use_cage and b.cage_extrusion <= 0:
        yield ("SCENE", "Bake", "extrusion à 0 sans cage")


# =========================================================================
# Organisation
# =========================================================================

_PREFIXES = ["Cube", "Plane", "Plan", "Cylinder", "Cylindre", "Sphere", "Sphère", "Icosphere", "Icosphère",
             "Cone", "Cône", "Torus", "Tore", "Circle", "Cercle", "Grid", "Grille", "Suzanne", "Monkey", "Singe",
             "Empty", "Vide", "Camera", "Caméra", "Light", "Lumière", "Point", "Sun", "Soleil", "Spot",
             "Projecteur", "Area", "Zone", "Text", "Texte", "BezierCurve", "BezierCircle", "Curve", "Courbe",
             "NurbsPath", "Mesh", "Maillage", "Object", "Objet", "Material", "Matériau", "Collection"]


@regle("noms_par_defaut", "Noms par défaut ou copies non renommées", "conseil",
       "Des noms comme Cube.003 ou Material.002 rendent la scène illisible pour soi, pour l'enseignant "
       "et pour un collaborateur ; on ne retrouve plus rien dans l'Outliner.",
       "Double-cliquer sur le nom dans l'Outliner (ou F2 dans la vue 3D) et donner un nom qui dit ce qu'est "
       "l'élément : « Poteau béton », « Spot couloir 01 ».",
       famille="Organisation", prefixes=_PREFIXES, signaler_copies=True)
def r_noms(ctx, p):
    motif = re.compile(r"^(?:%s)(?:\.\d{3})?$" % "|".join(re.escape(x) for x in p["prefixes"]))
    copie = re.compile(r"\.\d{3}$")
    objets = _objets(ctx["scene"])
    defaut = [o.name for o in objets if motif.match(o.name)]
    copies = [o.name for o in objets if not motif.match(o.name) and copie.search(o.name)] if p["signaler_copies"] else []
    if defaut:
        yield ("OBJETS", "\t".join(defaut), f"{len(defaut)} objet(s) au nom par défaut : {_liste(defaut)}")
    if copies:
        yield ("OBJETS", "\t".join(copies), f"{len(copies)} copie(s) non renommée(s) : {_liste(copies)}")
    mats = [n for n in ctx["mats"] if motif.match(n) or (p["signaler_copies"] and copie.search(n))]
    if mats:
        yield ("MATERIAUX", "\t".join(mats), f"{len(mats)} matériau(x) au nom générique : {_liste(mats)}")
    for c in ctx["scene"].collection.children_recursive:
        if motif.match(c.name):
            yield ("COLLECTION", c.name, f"collection « {c.name} »")


@regle("objets_racine", "Objets hors collection", "conseil",
       "Les objets posés à la racine de la scène ne sont rangés nulle part : on ne peut pas les masquer, "
       "les exclure du rendu ou les retrouver par groupe.",
       "Les sélectionner puis M > choisir ou créer une collection (« Éclairage », « Structure », « Mobilier »…).",
       famille="Organisation", max_objets=0)
def r_racine(ctx, p):
    racine = [o.name for o in ctx["scene"].collection.objects if o.library is None]
    if len(racine) > p["max_objets"]:
        yield ("OBJETS", "\t".join(racine), f"{len(racine)} objet(s) à la racine : {_liste(racine)}")


# =========================================================================
# Poids et géométrie : proportionner la scène à ce que voient les caméras
# =========================================================================

def _cameras(ctx, p):
    sc = ctx["scene"]
    if p.get("cameras", "toutes") == "active":
        return [sc.camera] if sc.camera else []
    return [o for o in _objets(sc) if o.type == "CAMERA"]


def _taille_image_rendue(sc):
    r = sc.render
    return r.resolution_x * r.resolution_percentage / 100.0, r.resolution_y * r.resolution_percentage / 100.0


def _couverture(ctx, o, cams):
    """(aire en px², plus grand côté en px, caméra) de la boîte englobante de l'objet,
    pour la caméra qui le voit le plus grand. None si aucune caméra ne le voit."""
    from bpy_extras.object_utils import world_to_camera_view
    cache = ctx.setdefault("_couv", {})
    cle = (o.name, tuple(c.name for c in cams))
    if cle in cache:
        return cache[cle]
    sc = ctx["scene"]
    W, H = _taille_image_rendue(sc)
    eo = o.evaluated_get(ctx["depsgraph"])
    coins = [eo.matrix_world @ Vector(c) for c in eo.bound_box]
    meilleur = None
    for cam in cams:
        pts = [world_to_camera_view(sc, cam, c) for c in coins]
        devant = [q for q in pts if q.z > 0]
        if not devant:
            continue
        if len(devant) < len(pts):          # l'objet entoure la caméra : il remplit le cadre
            x0, y0, x1, y1 = 0.0, 0.0, 1.0, 1.0
        else:
            x0 = max(0.0, min(q.x for q in pts)); x1 = min(1.0, max(q.x for q in pts))
            y0 = max(0.0, min(q.y for q in pts)); y1 = min(1.0, max(q.y for q in pts))
        if x1 <= x0 or y1 <= y0:
            continue
        dx, dy = (x1 - x0) * W, (y1 - y0) * H
        if meilleur is None or dx * dy > meilleur[0]:
            meilleur = (dx * dy, max(dx, dy), cam.name)
    cache[cle] = meilleur
    return meilleur


def _px_par_m_proche(ctx, o, cams):
    """Pixels par mètre au point de l'objet le plus proche d'une caméra en perspective (borne haute)."""
    sc = ctx["scene"]
    W, _ = _taille_image_rendue(sc)
    eo = o.evaluated_get(ctx["depsgraph"])
    coins = [eo.matrix_world @ Vector(c) for c in eo.bound_box]
    meilleur = 0.0
    for cam in cams:
        if cam.data.type != "PERSP":
            continue
        capteur = cam.data.sensor_width if cam.data.sensor_fit != "VERTICAL" else cam.data.sensor_height
        f_px = cam.data.lens / max(capteur, 1e-6) * W
        pc = cam.matrix_world.translation
        visee = -(cam.matrix_world.to_3x3() @ Vector((0, 0, 1)))
        profondeurs = [(c - pc).dot(visee) for c in coins]
        devant = [d for d in profondeurs if d > 0.05]
        if devant:
            meilleur = max(meilleur, f_px / min(devant))
    return meilleur


def _total_faces(ctx):
    """Faces de la scène visibles au rendu, modificateurs et instances (collections, Geometry Nodes) compris."""
    if "_total_faces" in ctx:
        return ctx["_total_faces"]
    total = sum(_faces_evaluees(ctx, o) for o in _rendus(ctx))
    for inst in ctx["depsgraph"].object_instances:
        if inst.is_instance and inst.object.type == "MESH" and inst.object.data is not None:
            total += len(inst.object.data.polygons)
    ctx["_total_faces"] = total
    return total


def _faces_evaluees(ctx, o):
    cache = ctx.setdefault("_faces", {})
    if o.name not in cache:
        eo = o.evaluated_get(ctx["depsgraph"])
        me = eo.data if eo.type == "MESH" else None
        cache[o.name] = len(me.polygons) if me is not None else 0
    return cache[o.name]


def _echelle_mapping(nt, tex):
    """Facteur de répétition d'un node Mapping en amont de la texture (produit des échelles X et Y)."""
    amont = _amont([tex])
    f = 1.0
    for n in nt.nodes:
        if n.name in amont and n.type == "MAPPING" and not n.inputs["Scale"].is_linked:
            s = n.inputs["Scale"].default_value
            f *= max(abs(s[0]), 1e-6) * max(abs(s[1]), 1e-6)
    return f


def _fmt_px(x):
    return f"{int(round(x))} px"


def _mil(x):
    """Entier avec espace des milliers."""
    return f"{int(x):,}".replace(",", " ")


@regle("texture_surdimensionnee", "Texture surdimensionnée", "conseil",
       "Une texture plus fine que ce que la caméra peut afficher alourdit le fichier et la mémoire sans rien "
       "ajouter à l'image : un objet de 1 m vu à 20 m n'occupe qu'une centaine de pixels, une texture 8k y est "
       "invisible.",
       "Réduire l'image dans un logiciel de retouche (ou en choisir une version plus petite : 1k, 2k) à la "
       "résolution indiquée, puis la recharger (Image > Recharger). Garder la grande version pour les gros plans.",
       famille="Poids", facteur_max=4.0, taille_min=2048, cameras="toutes")
def r_texture_surdim(ctx, p):
    cams = _cameras(ctx, p)
    if not cams:
        return
    besoins = {}   # image -> (besoin px, objet, caméra)
    hors_champ = {}
    for nom, (m, objs) in ctx["mats"].items():
        nt = m.node_tree
        att = _atteints_mat(ctx, m)
        texs = [n for n in nt.nodes if n.name in att and n.type == "TEX_IMAGE" and n.image is not None
                and n.image.source in ("FILE", "TILED", "GENERATED") and max(n.image.size) >= p["taille_min"]]
        if not texs:
            continue
        for o in objs:
            if o.type != "MESH" or o.hide_render:
                continue
            couv = _couverture(ctx, o, cams)
            for tex in texs:
                img = tex.image
                if couv is None:
                    hors_champ.setdefault(img.name, set()).add(o.name)
                    continue
                eo = o.evaluated_get(ctx["depsgraph"])
                taille_m = max(max(eo.dimensions), 1e-6)
                px_par_m = max(couv[1] / taille_m, min(_px_par_m_proche(ctx, o, cams), couv[1] * 4 / taille_m))
                st = _uv_stats(ctx, o)
                a3, a_uv = None, None
                if st is not None and st["a3"].sum() > 0:
                    idx = [i for i, s in enumerate(o.material_slots) if s.material == m]
                    masque = np.isin(st["mi"], idx)
                    if masque.any() and st.get("uv") is not None and tex.projection == "FLAT":
                        a3 = float(st["a3"][masque].sum())
                        a_uv = float(st["auv"][masque].sum())
                f = _echelle_mapping(nt, tex)
                if a3 and a_uv and a_uv > 1e-9:
                    besoin = px_par_m * math.sqrt(a3 / (a_uv * f))   # pixels d'écran par répétition de la texture
                else:
                    besoin = couv[1] / math.sqrt(f)                  # texture étalée sur l'objet
                if img.name not in besoins or besoin > besoins[img.name][0]:
                    besoins[img.name] = (besoin, o.name, couv[2])
    for nom, (besoin, obj, cam) in besoins.items():
        img = bpy.data.images[nom]
        taille = max(img.size)
        facteur = taille / max(besoin, 1.0)
        if facteur > p["facteur_max"]:
            conseil = 1 << max(8, math.ceil(math.log2(besoin * 2)))   # puissance de 2, avec une marge × 2
            yield ("OBJET", obj, f"« {nom} » : {img.size[0]} × {img.size[1]} px pour un besoin d'environ "
                                 f"{_fmt_px(besoin)} (caméra « {cam} ») : {_n(facteur, 0)} fois trop grande ; "
                                 f"{conseil} px suffiraient")
    for nom, objs in hors_champ.items():
        if nom not in besoins:
            img = bpy.data.images[nom]
            yield ("OBJETS", "\t".join(sorted(objs)), f"« {nom} » ({img.size[0]} × {img.size[1]} px) : "
                                                      f"sur des objets qu'aucune caméra ne voit ({_liste(sorted(objs))})")


@regle("densite_polygones", "Trop de polygones pour ce que voit la caméra", "conseil",
       "Un objet très détaillé qui n'occupe que quelques pixels à l'image alourdit le fichier, la mémoire et "
       "l'affichage sans rien apporter au rendu. Au-delà de quelques millions de faces, la scène devient "
       "pénible à manipuler sur un portable.",
       "Réduire le détail des objets lointains : moins de subdivisions (modificateur Subdivision, niveau de rendu), "
       "modificateur Décimer, ou une version simplifiée de l'objet. Garder le détail pour les gros plans.",
       famille="Poids", faces_par_px=2.0, faces_min=20000, total_max=3000000, cameras="toutes")
def r_densite(ctx, p):
    cams = _cameras(ctx, p)
    for o in _rendus(ctx):
        f = _faces_evaluees(ctx, o)
        if f < p["faces_min"] or not cams:
            continue
        couv = _couverture(ctx, o, cams)
        if couv is None:
            yield ("OBJET", o.name, f"{_mil(f)} faces sur un objet qu'aucune caméra ne voit")
            continue
        d = f / max(couv[0], 1.0)
        if d > p["faces_par_px"]:
            yield ("OBJET", o.name, f"{_mil(f)} faces pour environ {_mil(couv[0])} px² à l'image (caméra « {couv[2]} ») : "
                                    f"{_n(d, 1)} face(s) par pixel")
    total = _total_faces(ctx)
    if p["total_max"] and total > p["total_max"]:
        yield ("SCENE", "Scène", f"{_mil(total)} faces au total, instances comprises (seuil : {_mil(p['total_max'])})")


def _signature(o):
    """Même géométrie (sommets et ordre des faces), même échelle, mêmes matériaux, mêmes modificateurs."""
    me = o.data
    n = len(me.vertices)
    if n == 0 or n > 300000:
        return None
    co = np.empty(n * 3, dtype=np.float32); me.vertices.foreach_get("co", co)
    lv = np.empty(len(me.loops), dtype=np.int32); me.loops.foreach_get("vertex_index", lv)
    echelle = tuple(round(x, 3) for x in o.scale)
    mats = tuple(s.material.name if s.material else "" for s in o.material_slots)
    mods = tuple(m.type for m in o.modifiers)
    return (n, len(me.polygons), hash(np.round(co, 4).tobytes()), hash(lv.tobytes()), echelle, mats, mods)


@regle("copies_non_liees", "Copies non liées", "conseil",
       "Des objets identiques dupliqués avec Maj+D ont chacun leur propre maillage : le fichier stocke autant "
       "de copies que d'objets, et une modification doit être refaite sur chacun.",
       "Dupliquer avec Alt+D (copie liée) ; pour lier des copies existantes : sélectionner les copies, puis "
       "en dernier l'objet modèle, Ctrl+L > Lier les données d'objet (Link Object Data).",
       famille="Poids", groupe_min=3)
def r_copies(ctx, p):
    groupes = {}
    for o in _objets(ctx["scene"]):
        if o.type != "MESH" or o.data is None or o.data.library is not None:
            continue
        sig = _signature(o)
        if sig is None:
            continue
        groupes.setdefault(sig, {}).setdefault(o.data.name, []).append(o.name)
    for sig, par_maillage in groupes.items():
        if len(par_maillage) >= p["groupe_min"]:
            objs = sorted(n for noms in par_maillage.values() for n in noms)
            yield ("OBJETS", "\t".join(objs), f"{len(par_maillage)} maillages identiques ({sig[1]} faces chacun) "
                                              f"au lieu d'un seul : {_liste(objs)}")


def _images_atteintes(ctx):
    res = set()
    for nom, (m, objs) in ctx["mats"].items():
        att = _atteints_mat(ctx, m)
        for n in m.node_tree.nodes:
            if n.name in att and n.type == "TEX_IMAGE" and n.image is not None:
                res.add(n.image.name)
    w = ctx["scene"].world
    if w and w.node_tree:
        for n in w.node_tree.nodes:
            if n.type in ("TEX_ENVIRONMENT", "TEX_IMAGE") and n.image is not None:
                res.add(n.image.name)
    return res


def _memoire_image(img):
    w, h = img.size
    return w * h * 4 * (4 if img.is_float else 1)


@regle("donnees_inutilisees", "Données inutilisées gardées dans le fichier", "conseil",
       "Images, matériaux ou maillages gardés sans servir alourdissent le fichier et brouillent les listes. "
       "Blender les conserve quand ils sont protégés (bouclier « faux utilisateur ») ou restent branchés à des "
       "nodes qui ne mènent nulle part.",
       "Fichier > Nettoyer > Données orphelines récursives (File > Clean Up > Purge Unused Data) ; pour une donnée "
       "protégée, retirer le bouclier dans le sélecteur, puis nettoyer. Supprimer aussi les nodes d'image non reliés.",
       famille="Poids")
def r_inutilisees(ctx, p):
    atteintes = _images_atteintes(ctx)
    imgs = [i for i in bpy.data.images if i.library is None and i.type in ("IMAGE", "UV_TEST") and i.users > 0
            and i.name not in atteintes and i.source in ("FILE", "TILED", "GENERATED")]
    if imgs:
        mo = sum(_memoire_image(i) for i in imgs) / 2 ** 20
        emballe = sum(1 for i in imgs if i.packed_file is not None)
        detail = f"{len(imgs)} image(s) qui ne servent à aucun rendu ({_liste([i.name for i in imgs])}), environ {_n(mo, 0)} Mo en mémoire"
        if emballe:
            detail += f", dont {emballe} empaquetée(s) dans le fichier"
        yield ("SCENE", "Images", detail)
    proteges = [x.name for coll in (bpy.data.materials, bpy.data.meshes, bpy.data.node_groups)
                for x in coll if x.library is None and x.use_fake_user and x.users <= 1]
    if proteges:
        yield ("SCENE", "Données", f"{len(proteges)} donnée(s) gardées seulement par le bouclier : {_liste(proteges)}")
    for o in _objets(ctx["scene"]):
        if o.type != "MESH" or not o.material_slots or o.data is None or not len(o.data.polygons):
            continue
        mi = np.empty(len(o.data.polygons), dtype=np.int64)
        o.data.polygons.foreach_get("material_index", mi)
        utilises = set(np.unique(mi).tolist())
        vides = [s.material.name for i, s in enumerate(o.material_slots) if s.material and i not in utilises]
        if vides:
            yield ("OBJET", o.name, f"matériau(x) attribué(s) à aucune face : {_liste(vides)}")


@regle("geometrie", "Géométrie à reprendre", "conseil",
       "Des normales inversées assombrissent ou font disparaître des faces (et faussent le Line Art et le cel "
       "shading) ; des sommets en double créent des arêtes dures et des artefacts d'ombre ; des faces non planes "
       "se plient de façon imprévisible au rendu.",
       "Mode Édition, tout sélectionner (A) : Maillage > Normales > Recalculer à l'extérieur (Maj+N) ; "
       "Maillage > Nettoyer > Fusionner par distance (M) ; pour les faces non planes : Maillage > Nettoyer > "
       "Rendre planes, ou trianguler (Ctrl+T).",
       famille="Objets", tolerance_doublons=0.0001, ecart_planeite=0.02, sommets_max=500000)
def r_geometrie(ctx, p):
    for o in _rendus(ctx):
        me = o.data
        nv, nf, nl = len(me.vertices), len(me.polygons), len(me.loops)
        if nv == 0 or nf == 0 or nv > p["sommets_max"]:
            continue
        problemes = []
        co = np.empty(nv * 3, dtype=np.float64); me.vertices.foreach_get("co", co); co = co.reshape(-1, 3)
        # sommets en double
        q = np.round(co / p["tolerance_doublons"]).astype(np.int64)
        _, inv, cnt = np.unique(q, axis=0, return_inverse=True, return_counts=True)
        doublons = int((cnt - 1)[cnt > 1].sum())
        if doublons:
            problemes.append(f"{doublons} sommet(s) en double")
        # orientation des faces (normales incohérentes ou inversées)
        ls = np.empty(nf, dtype=np.int64); me.polygons.foreach_get("loop_start", ls)
        lt = np.empty(nf, dtype=np.int64); me.polygons.foreach_get("loop_total", lt)
        lv = np.empty(nl, dtype=np.int64); me.loops.foreach_get("vertex_index", lv)
        nxt = np.arange(nl) + 1; nxt[ls + lt - 1] = ls
        a, b = lv, lv[nxt]
        orientees = a.astype(np.int64) * (nv + 1) + b
        _, c_or = np.unique(orientees, return_counts=True)
        incoherentes = int((c_or > 1).sum())
        if incoherentes:
            problemes.append(f"{incoherentes} arête(s) entre faces d'orientation opposée (normales incohérentes)")
        else:
            aretes = np.minimum(a, b).astype(np.int64) * (nv + 1) + np.maximum(a, b)
            _, c_ar = np.unique(aretes, return_counts=True)
            if (c_ar == 2).all():                       # volume fermé : le signe du volume donne le sens
                poly = np.repeat(np.arange(nf), lt)
                debut = co[lv[np.repeat(ls, lt)]]
                p1, p2 = co[a], co[b]
                vol = float(np.einsum("ij,ij->i", debut, np.cross(p1, p2)).sum()) / 6.0
                if vol < 0:
                    problemes.append("normales inversées (tournées vers l'intérieur du volume)")
        # faces non planes (quadrilatères et n-gones)
        grand = lt >= 4
        if grand.any():
            nrm = np.empty(nf * 3); me.polygons.foreach_get("normal", nrm); nrm = nrm.reshape(-1, 3)
            ctr = np.empty(nf * 3); me.polygons.foreach_get("center", ctr); ctr = ctr.reshape(-1, 3)
            ar = np.empty(nf); me.polygons.foreach_get("area", ar)
            poly = np.repeat(np.arange(nf), lt)
            dist = np.abs(np.einsum("ij,ij->i", co[lv] - ctr[poly], nrm[poly]))
            dmax = np.zeros(nf); np.maximum.at(dmax, poly, dist)
            ratio = dmax / np.sqrt(np.maximum(ar, 1e-12))
            np_ = int(((ratio > p["ecart_planeite"]) & grand).sum())
            if np_:
                problemes.append(f"{np_} face(s) non plane(s)")
        if problemes:
            yield ("OBJET", o.name, " ; ".join(problemes))


@regle("poids_fichier", "Poids du fichier", "acquis",
       "Un fichier maîtrisé est un fichier léger : il s'ouvre, s'enregistre et se partage vite, et tient dans "
       "la mémoire d'un portable.",
       "Voir les règles « Texture surdimensionnée », « Trop de polygones », « Copies non liées » et "
       "« Données inutilisées » : ce sont elles qui expliquent le poids.",
       famille="Poids", fichier_max_mo=0.0, textures_max_mo=0.0, faces_max=0)
def r_poids(ctx, p):
    taille = os.path.getsize(bpy.data.filepath) / 2 ** 20 if bpy.data.filepath and os.path.isfile(bpy.data.filepath) else None
    atteintes = _images_atteintes(ctx)
    tex_mo = sum(_memoire_image(bpy.data.images[n]) for n in atteintes if n in bpy.data.images) / 2 ** 20
    total = _total_faces(ctx)
    morceaux = [f"fichier : {_n(taille, 1)} Mo" if taille is not None else "fichier non enregistré",
                f"{_mil(total)} faces (instances comprises)",
                f"textures utilisées : environ {_n(tex_mo, 0)} Mo en mémoire"]
    depasse = ((p["fichier_max_mo"] and taille and taille > p["fichier_max_mo"])
               or (p["textures_max_mo"] and tex_mo > p["textures_max_mo"])
               or (p["faces_max"] and total > p["faces_max"]))
    yield ("SCENE", "Fichier", " · ".join(morceaux), "avertissement" if depasse else "acquis")

# =========================================================================
# Livrables et rendu non réaliste
# =========================================================================

_NOMS_CAMERA = re.compile(r"^(?:Camera|Caméra)(?:\.\d{3})?$")


@regle("cameras_nommees", "Caméras des images à rendre", "bloquant",
       "Chaque image demandée doit avoir sa caméra, nommée d'après l'image : on retrouve et on refait "
       "le rendu sans chercher, et l'enseignant sait quelle vue correspond à quelle image.",
       "Ajouter une caméra par image (Ajouter > Caméra), la cadrer, puis la renommer dans l'Outliner "
       "(« CAM_mixte », « CAM_variante »).",
       actif=False, famille="Caméras", minimum=2)
def r_cameras_nommees(ctx, p):
    cams = [o for o in _objets(ctx["scene"]) if o.type == "CAMERA"]
    nommees = [o.name for o in cams if not _NOMS_CAMERA.match(o.name)]
    if len(nommees) < p["minimum"]:
        anonymes = [o.name for o in cams if _NOMS_CAMERA.match(o.name)]
        detail = f"{len(nommees)} caméra(s) nommée(s) sur {p['minimum']} attendue(s)"
        if anonymes:
            detail += f" ; au nom par défaut : {_liste(anonymes)}"
        yield ("SCENE", "Caméras", detail)
    else:
        yield ("SCENE", "Caméras", f"{len(nommees)} caméras nommées : {_liste(nommees)}", "acquis")


def _techniques_non_realistes(ctx):
    """[(type_cible, cible, technique)] des modes de rendu non réalistes présents dans la scène."""
    sc = ctx["scene"]
    res = []
    if sc.render.use_freestyle:
        res.append(("SCENE", "Rendu", "Freestyle (contours)"))
    for o in _objets(sc):
        for m in getattr(o, "modifiers", []):
            t = m.type
            if t == "WIREFRAME" and o.type == "MESH":
                res.append(("OBJET", o.name, "modificateur Filaire (Wireframe)"))
            elif t in ("LINEART", "GREASE_PENCIL_LINEART") or "LINEART" in t:
                res.append(("OBJET", o.name, "Line Art"))
            elif t == "SOLIDIFY" and o.type == "MESH" and m.use_flip_normals:
                res.append(("OBJET", o.name, "contour par coque inversée (Solidify inversé)"))
        for m in getattr(o, "grease_pencil_modifiers", []) or []:
            if "LINEART" in m.type:
                res.append(("OBJET", o.name, "Line Art"))
    for nom, (m, objs) in ctx["mats"].items():
        types = {n.type for n in m.node_tree.nodes if n.name in _atteints_mat(ctx, m)}
        if "SHADER_TO_RGB" in types:
            res.append(("MATERIAU", nom, "cel shading (Shader vers RVB)"))
        if "BSDF_TOON" in types:
            res.append(("MATERIAU", nom, "shader Toon"))
        if "WIREFRAME" in types:
            res.append(("MATERIAU", nom, "node Filaire (Wireframe)"))
    return res


@regle("rendu_non_realiste", "Rendu non réaliste", "acquis",
       "L'image doit associer des matériaux PBR et au moins un mode de représentation non réaliste.",
       "Ajouter un mode non réaliste : Freestyle (Propriétés > Rendu), un objet Line Art, un modificateur "
       "Filaire, ou un matériau en cel shading (Shader vers RVB suivi d'une Color Ramp constante).",
       actif=False, famille="Rendu", niveau_si_absent="avertissement")
def r_non_realiste(ctx, p):
    trouves = _techniques_non_realistes(ctx)
    if not trouves:
        yield ("SCENE", "Rendu", "aucun mode de rendu non réaliste détecté", p["niveau_si_absent"])
    for cible_type, cible, technique in trouves:
        yield (cible_type, cible, technique)


# =========================================================================
# Journal de travail
# =========================================================================

@regle("journal_travail", "Journal de travail", "avertissement",
       "Le journal retrace l'histoire du fichier : quand il a été enregistré, comment il a grossi, sur "
       "quelles machines. Il aide à retrouver l'arrivée d'un problème et, dans un cadre d'évaluation, "
       "montre que le travail s'est construit dans le temps.",
       "Garder DiagScene installé et le journal activé (Préférences > Add-ons > DiagScene) pendant tout le "
       "travail, et enregistrer régulièrement (Ctrl+S).",
       actif=False, famille="Journal", min_jours=2, min_enregistrements=5, niveau_absent="bloquant",
       niveau_altere="avertissement")
def r_journal(ctx, p):
    from . import journal
    if not journal.actif():
        yield ("SCENE", "Journal", "journal désactivé dans les préférences de DiagScene", p["niveau_absent"])
    s = journal.synthese()
    if s is None:
        yield ("SCENE", "Journal", "aucun journal dans ce fichier (il commence au prochain enregistrement)",
               p["niveau_absent"])
        return
    for pb in s["problemes"]:
        yield ("SCENE", "Journal", f"journal altéré : {pb}", p["niveau_altere"])
    if len(s["jours"]) < p["min_jours"]:
        yield ("SCENE", "Journal", f"travail enregistré sur {len(s['jours'])} jour(s) "
                                   f"(attendu : au moins {p['min_jours']})")
    if s["enregistrements"] < p["min_enregistrements"]:
        yield ("SCENE", "Journal", f"{s['enregistrements']} enregistrement(s) journalisé(s) "
                                   f"(attendu : au moins {p['min_enregistrements']})")
    if s["integre"] and len(s["jours"]) >= p["min_jours"] and s["enregistrements"] >= p["min_enregistrements"]:
        yield ("SCENE", "Journal", f"{s['enregistrements']} enregistrements sur {len(s['jours'])} jours", "acquis")


# =========================================================================
# Moteur
# =========================================================================

def analyser(scene, view_layer, config, depsgraph=None):
    cfg = config.get("regles", {})
    seulement_listees = bool(config.get("meta", {}).get("seulement_listees", False))
    ctx = {"scene": scene, "view_layer": view_layer, "mats": _materiaux_utilises(scene),
           "depsgraph": depsgraph or bpy.context.evaluated_depsgraph_get(),
           "selection": [o for o in view_layer.objects if o.select_get(view_layer=view_layer)]}
    alertes = []
    for rid in cfg:
        if rid not in DEFAUTS:
            alertes.append(dict(regle="configuration", famille="Configuration", niveau="conseil",
                                titre="Règle inconnue dans la configuration",
                                cible_type="SCENE", cible="", detail=f"« {rid} » (faute de frappe ?)",
                                pourquoi="La configuration mentionne une règle que cette version de l'extension ne connaît pas.",
                                comment="Vérifier le nom de la règle dans le fichier de configuration.", lien=""))
    for rid, fn in REGLES:
        d, c = DEFAUTS[rid], cfg.get(rid, {})
        actif_defaut = False if seulement_listees and rid not in cfg else d["actif"]
        if seulement_listees and rid in cfg and "actif" not in c:
            actif_defaut = True
        if not c.get("actif", actif_defaut):
            continue
        params = dict(d["params"])
        params.update({k: v for k, v in c.items() if k in params})
        niveau = c.get("niveau", d["niveau"])
        if niveau not in NIVEAUX:
            niveau = d["niveau"]
        textes = {k: c.get(k, d[k]) for k in ("titre", "pourquoi", "comment", "lien")}
        try:
            for res in fn(ctx, params):
                cible_type, cible, detail = res[:3]
                n = res[3] if len(res) > 3 and res[3] in NIVEAUX else niveau
                alertes.append(dict(regle=rid, famille=d["famille"], niveau=n, cible_type=cible_type,
                                    cible=cible, detail=detail, **textes))
        except Exception as e:
            alertes.append(dict(regle=rid, famille=d["famille"], niveau="conseil", titre=f"Règle « {rid} » non vérifiée",
                                cible_type="SCENE", cible="", detail=f"erreur interne : {type(e).__name__} : {e}",
                                pourquoi="", comment="Signaler ce message à l'enseignant.", lien=""))
    ordre = {n: i for i, n in enumerate(NIVEAUX)}
    alertes.sort(key=lambda a: ordre[a["niveau"]])
    return alertes
