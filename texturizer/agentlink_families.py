# SPDX-License-Identifier: AGPL-3.0-only
# What Agent Link for FreeCAD loads from Texturizer when both are installed:
# the texture tools (texture_prepare, texture_check) and their guide. Agent
# Link finds this module by convention and never names it; without Agent
# Link it is never loaded.
from . import CREDIT as _CREDIT
from . import fam_texture

AGENTLINK_API = ">=1,<2"
FAMILIES = [fam_texture]
CREDIT = _CREDIT
LICENSE = "AGPL-3.0-only"

