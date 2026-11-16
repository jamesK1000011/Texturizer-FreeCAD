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
# The Texture panel is Texturizer's own dock beside Model and Tasks (its
# clicks run through Agent Link, panel.runner); only an Agent Link that
# cannot place it hosts it as a tab of its panel.
from . import layout as _layout  # noqa: E402

if _layout.hosted_tabs(hooks):
    # The Texture page carries its own credit line (BumpMesh by Stefan
    # Hermann, CNC Kitchen), so own_credit=True: Agent Link hides its own
    # credit line while this tab is in front.
    try:  # the tab only while Texturizer is the workbench in front
        hooks.register_tab("Texture", _texture_tab, order=60, workbench="TexturizerWorkbench",
                           own_credit=True)
    except TypeError:  # an Agent Link before own_credit, or before tabs per workbench
        try:
            hooks.register_tab("Texture", _texture_tab, order=60, workbench="TexturizerWorkbench")
        except TypeError:
            hooks.register_tab("Texture", _texture_tab, order=60)
