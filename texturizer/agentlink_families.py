# SPDX-License-Identifier: AGPL-3.0-only
# What Agent Link for FreeCAD loads from Texturizer when both are installed:
# the texture tools (texture_prepare, texture_check), their guide, and the
# Texture tab in Agent Link's panel. Agent Link finds this module by
# convention and never names it; without Agent Link it is never loaded, and
# Texturizer shows its panel in a dock of its own.
import texturizer
from agentlink import hooks

from . import CREDIT as _CREDIT
from . import fam_texture

AGENTLINK_API = ">=1,<2"
FAMILIES = [fam_texture]
CREDIT = _CREDIT
LICENSE = "AGPL-3.0-only"


def _texture_tab(panel):
    from . import panel as texture_panel

    return texture_panel.hosted_factory(panel)


texturizer._hosted = True
hooks.register_tab("Texture", _texture_tab, order=60)
