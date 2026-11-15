# SPDX-License-Identifier: AGPL-3.0-only
# Texturizer's own copy of Agent Link for FreeCAD's refusal.py, trimmed to what Texturizer uses; 14 Nov 2026.
# A refusal that says what to do instead.


class Refused(ValueError):
    """A request this add-on will not carry out, with what to do instead.
    code: invalid_input, not_found, invalid_state, unavailable. Agent Link
    turns it into a refusal (any exception with code and message)."""

    def __init__(self, message, code="invalid_input"):
        super().__init__(message)
        self.code = code
        self.message = message

