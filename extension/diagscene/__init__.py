# SPDX-License-Identifier: GPL-3.0-or-later
# DiagScene : pointe ce qui ne va pas dans un fichier Blender, sans rien corriger.

from . import ui


def register():
    ui.register()


def unregister():
    ui.unregister()
