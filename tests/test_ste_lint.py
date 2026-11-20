# SPDX-License-Identifier: AGPL-3.0-only
# The tool descriptions in ASD-STE100 (branch explore/full-ste): at most 20
# words a sentence, and nothing of the original text lost (numbers, units,
# input names, tool names, code names, guard words). scripts/ste_check.py
# does the work; scripts/ste_originals.json is the text main held before.
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))
import ste_check as S  # noqa: E402


class SteLint(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.descs = S.current()
        cls.originals = S.load_originals()

    def test_every_original_tool_is_read(self):
        self.assertEqual(set(self.originals["tools"]) - set(self.descs), set())
        self.assertGreater(len(self.descs), 0)

    def test_sentences_have_at_most_20_words(self):
        faults = S.lint(self.descs, self.originals.get("toolNames", ()))
        self.assertEqual(faults, [], "\n".join(f"{t}: {x}" for t, _, x in faults))

    def test_nothing_of_the_original_is_lost(self):
        faults = S.preservation(self.descs, self.originals)
        self.assertEqual(faults, [], "\n".join(f"{t}: {k}: {x}" for t, k, x in faults))

    def test_every_waiver_has_a_reason(self):
        for key, reason in S.WAIVERS.items():
            self.assertTrue(isinstance(reason, str) and len(reason.split()) >= 3, key)

    def test_the_lint_counts_as_it_says(self):
        # The hub's counting: names in a run of three or more count as one word;
        # quotes, backticks and {...} / [...] literals are not counted.
        self.assertEqual(S.words("Inputs: path, object, units and name.", {"path", "object", "units", "name"}), 2)
        self.assertEqual(S.words("Inputs: path, object, units and name."), 6)
        self.assertEqual(S.words('Give "a quoted literal of many many many words here".'), 1)
        self.assertEqual(S.words("The answer holds { a, b, c, d, e, f }."), 3)
        self.assertEqual(S.words("Tools: scan_open, scan_qa, mesh_densify and maxEdge."), 2)
        self.assertEqual(len(S.sentences("One idea. two: lower case starts a sentence too. For example, e.g. this stays.")), 3)
        self.assertEqual(len(S.sentences("Keys { a. b. c } stay one. 0.5 mm is one.")), 2)

    def test_the_preservation_check_finds_a_loss(self):
        # A tool whose card has no guide section (GUIDE_SECTIONS): the
        # original text alone must pass.
        name = sorted(set(self.originals["tools"]) - set(S.GUIDE_SECTIONS))[0]
        old = self.originals["tools"][name]
        cut = {name: dict(old, description="Nothing.")}
        found = S.preservation(cut, {"toolNames": [], "tools": {name: old}})
        if S._numbers(old["description"]) or S._code(old["description"]) or S._guards(old["description"]):
            self.assertTrue(found)
        kept = {name: dict(old)}
        self.assertEqual(S.preservation(kept, {"toolNames": [], "tools": {name: old}}), [])
        self.assertIn((name, "number", "750"), S.preservation({name: dict(old, description="x")}, {"toolNames": [], "tools": {name: dict(old, description="750 000")}}))
        self.assertIn((name, "guard", "never"), S.preservation({name: dict(old, description="x")}, {"toolNames": [], "tools": {name: dict(old, description="It never runs.")}}))


if __name__ == "__main__":
    unittest.main()


class ShortCards(unittest.TestCase):
    """Desk-lamp test drive, finding 14: texture_apply's and texture_set's
    cards were thousands of words, read whole by one tool_find. The cards
    keep the inputs, what the tool makes, the traps and the answer; the rest
    is a guide section, which the card names and the check reads with it."""

    @classmethod
    def setUpClass(cls):
        cls.descs = S.current()

    def test_cards_are_short_and_name_their_section(self):
        for name, cap in (("texture_apply", 400), ("texture_set", 220)):
            text = self.descs[name]["description"]
            self.assertLessEqual(len(text.split()), cap, name)
            guide, section = S.GUIDE_SECTIONS[name]
            self.assertIn(f'guide_read {{ topic: "texture", section: "{section}" }}', text)
            self.assertEqual(guide, "texturizer/guides/texture.md")

    def test_the_section_is_there_and_read_later(self):
        for name in S.GUIDE_SECTIONS:
            body = S.guide_section(name)
            self.assertIsNotNone(body, name)
            self.assertGreater(len(body.split()), 150, name)
        with open(os.path.join(S.ROOT, "texturizer", "guides", "texture.md"), encoding="utf-8") as f:
            text = f.read()
        for section in ("texture_apply in full", "texture_set in full"):
            head = text.index(f"## {section}\n")
            self.assertTrue(text[head:].split("\n", 2)[1].startswith("<!-- later:"), section)

    def test_a_card_without_its_section_is_a_fault(self):
        keep = dict(S.GUIDE_SECTIONS)
        try:
            S.GUIDE_SECTIONS["texture_set"] = ("texturizer/guides/texture.md", "no such section")
            faults = S.preservation(self.descs, S.load_originals())
            self.assertIn("guide", {k for t, k, _ in faults if t == "texture_set"})
        finally:
            S.GUIDE_SECTIONS.clear()
            S.GUIDE_SECTIONS.update(keep)

    def test_texture_last_then_slice_the_textured_mesh(self):
        # slice_with (Workshop for FreeCAD) takes a mesh as it is now; the
        # card says so, and the other way: export the file and slice it.
        text = self.descs["texture_apply"]["description"]
        self.assertIn("texture last", text)
        self.assertIn("slice_with (Workshop for FreeCAD) takes the Textured object as it is", text)
        self.assertIn("texture_export", text)
        self.assertNotIn("then slice the mesh", text)
