# SPDX-License-Identifier: AGPL-3.0-only
# The helper files Agent Link shares with its add-ons (Agent Link's
# scripts/sync_shared.py lists them) have one copy here, equal to Agent
# Link's but for the first (licence) line. Agent Link is looked for beside
# this repository (or at $AGENTLINK_DIR / $AGENTLINK_SRC); without it the
# check is skipped. Fix a difference with Agent Link's
#   python3 scripts/sync_shared.py <this repository>
import ast
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
SKIP = {".git", "__pycache__", "tests", "build", "dist", "node_modules"}


def agentlink():
    for d in (os.environ.get("AGENTLINK_DIR"), os.environ.get("AGENTLINK_SRC"),
              os.path.join(os.path.dirname(ROOT), "agentlink-freecad"), os.path.join(os.path.dirname(ROOT), "AgentLink-FreeCAD"),
              os.path.join(ROOT, "agentlink-addon")):
        if d and os.path.isfile(os.path.join(d, "scripts", "sync_shared.py")):
            return d
    return None


def shared_list(al):
    """Agent Link's SHARED: {its file: the line every copy carries}."""
    tree = ast.parse(open(os.path.join(al, "scripts", "sync_shared.py"), encoding="utf-8").read())
    for n in tree.body:
        if isinstance(n, ast.Assign) and any(getattr(t, "id", None) == "SHARED" for t in n.targets):
            return ast.literal_eval(n.value)
    raise AssertionError("no SHARED in sync_shared.py")


def body(path):
    with open(path, encoding="utf-8") as f:
        return f.read().split("\n", 1)[1]


def copies(name, mark):
    out = []
    for d, dirs, files in os.walk(ROOT):
        dirs[:] = [x for x in dirs if x not in SKIP]
        for f in files:
            if f == name:
                p = os.path.join(d, f)
                if mark in open(p, encoding="utf-8").read():
                    out.append(p)
    return out


class Shared(unittest.TestCase):
    def test_vendored_files_match_agent_link(self):
        al = agentlink()
        if al is None:
            self.skipTest("Agent Link is not checked out beside this repository")
        shared = shared_list(al)
        self.assertTrue(shared)
        for name, mark in shared.items():
            found = copies(os.path.basename(name), mark)
            self.assertEqual(len(found), 1, f"one copy of {name} in the add-on, found {found}")
            self.assertEqual(body(found[0]), body(os.path.join(al, name)),
                             f"{os.path.relpath(found[0], ROOT)} differs from Agent Link's {name}: run Agent Link's scripts/sync_shared.py on this repository")

    def test_the_copy_is_texturizers(self):
        text = open(os.path.join(ROOT, "texturizer", "layout.py"), encoding="utf-8").read()
        self.assertTrue(text.startswith("# SPDX-License-Identifier: AGPL-3.0-only\n"))
        self.assertIn("This file is the same in every add-on but for its first line", text)


if __name__ == "__main__":
    unittest.main()
