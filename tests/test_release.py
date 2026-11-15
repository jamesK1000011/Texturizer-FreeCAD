# SPDX-License-Identifier: AGPL-3.0-only
# Release readiness without FreeCAD: the README's tool list is current, the
# licence of every file is stated (a REUSE-style check, for when the `reuse`
# tool is not installed), package.xml is what the Addon Manager needs, and the
# changelog has an Unreleased section.
import fnmatch
import os
import re
import subprocess
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

import gen_readme_tools  # noqa: E402

MAIN = "AGPL-3.0-only"
SOURCE = (".py", ".js", ".mjs", ".sh")


def tracked():
    try:
        out = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError):
        return None
    return [f for f in out.splitlines() if os.path.exists(os.path.join(ROOT, f))]


def reuse_annotations():
    """(globs, licence) pairs from REUSE.toml, read without tomllib."""
    out, globs = [], None
    for line in open(os.path.join(ROOT, "REUSE.toml"), encoding="utf-8"):
        m = re.match(r"\s*path\s*=\s*(.+)", line)
        if m:
            globs = re.findall(r'"([^"]+)"', m.group(1))
        m = re.match(r'\s*SPDX-License-Identifier\s*=\s*"([^"]+)"', line)
        if m and globs is not None:
            out.append((globs, m.group(1)))
            globs = None
    return out


def _match(path, glob):
    rx = re.escape(glob).replace(r"\*\*", "\0").replace(r"\*", "[^/]*").replace("\0", ".*")
    return re.fullmatch(rx, path) is not None


class Readme(unittest.TestCase):
    def test_check_flag(self):
        self.assertEqual(gen_readme_tools.main(["--check"]), 0, "run python3 tools/gen_readme_tools.py")

    def test_tool_list_is_current(self):
        text = open(os.path.join(ROOT, "README.md"), encoding="utf-8").read()
        self.assertEqual(gen_readme_tools.render(text), text, "run python3 tools/gen_readme_tools.py")

    def test_tool_list_is_every_tool(self):
        import json

        with open(os.path.join(ROOT, "texturizer", "data", "tools.json"), encoding="utf-8") as f:
            names = list(json.load(f)["entries"])
        self.assertEqual(re.findall(r"(?m)^\| `(\w+)` \|", gen_readme_tools.block()), names)


class Licences(unittest.TestCase):
    def test_every_file_has_a_licence(self):
        files = tracked()
        if files is None:
            self.skipTest("not a git checkout")
        notes = reuse_annotations()
        missing, wrong = [], []
        for f in files:
            if f in ("LICENSE", "LICENSE-DATA") or f.startswith("LICENSES/"):
                continue
            with open(os.path.join(ROOT, f), "rb") as fh:
                head = fh.read(600).decode("utf-8", "replace")
            m = re.search(r"SPDX-License-Identifier:\s*([^\s*>-][^\s>]*)", head)
            if m:
                ids = [m.group(1)]
            else:
                ids = [lic for globs, lic in notes if any(_match(f, g) for g in globs)]
                if f.endswith(SOURCE) or not ids:
                    missing.append(f)
                    continue
            for i in ids:
                for part in re.split(r"\s+(?:AND|OR|WITH)\s+", i):
                    if not os.path.exists(os.path.join(ROOT, "LICENSES", part + ".txt")):
                        wrong.append(f"{f}: {part}")
        self.assertEqual(missing, [], "no SPDX header or REUSE.toml entry")
        self.assertEqual(wrong, [], "licence text not in LICENSES/")

    def test_main_licence_is_agpl(self):
        head = open(os.path.join(ROOT, "LICENSE"), encoding="utf-8").read(400)
        self.assertIn("GNU AFFERO GENERAL PUBLIC LICENSE", head)
        self.assertIn("Version 3", head)
        with open(os.path.join(ROOT, "LICENSES", MAIN + ".txt"), encoding="utf-8") as f:
            self.assertEqual(f.read(), open(os.path.join(ROOT, "LICENSE"), encoding="utf-8").read())

    def test_data_licence_matches_reuse(self):
        # LICENSE-DATA puts the data files under CC BY 4.0; REUSE.toml says the same.
        self.assertIn("CC BY 4.0", open(os.path.join(ROOT, "LICENSE-DATA"), encoding="utf-8").read())
        notes = reuse_annotations()
        for f in ("texturizer/data/tools.json",):
            self.assertIn("CC-BY-4.0", [lic for globs, lic in notes if any(_match(f, g) for g in globs)], f)

    def test_no_bytecode_tracked(self):
        files = tracked()
        if files is None:
            self.skipTest("not a git checkout")
        self.assertEqual([f for f in files if "__pycache__" in f or f.endswith(".pyc")], [])


class Changelog(unittest.TestCase):
    def test_unreleased_section(self):
        text = open(os.path.join(ROOT, "CHANGELOG.md"), encoding="utf-8").read()
        self.assertRegex(text, r"(?m)^## \[?Unreleased\]?")

    def test_version_released(self):
        text = open(os.path.join(ROOT, "CHANGELOG.md"), encoding="utf-8").read()
        src = open(os.path.join(ROOT, PKG, "__init__.py"), encoding="utf-8").read()
        version = re.search(r'^VERSION\s*=\s*"([^"]+)"', src, re.M).group(1)
        self.assertRegex(text, r"(?m)^## \[?%s\]?" % re.escape(version))


PKG = 'texturizer'
URLS = ("repository", "bugtracker", "readme")
NS = {"p": "https://wiki.freecad.org/Package_Metadata"}


class PackageXml(unittest.TestCase):
    """package.xml as FreeCAD's Addon Manager reads it."""

    @classmethod
    def setUpClass(cls):
        import xml.etree.ElementTree as ET

        cls.root = ET.parse(os.path.join(ROOT, "package.xml")).getroot()

    def one(self, tag, under=None):
        el = (under if under is not None else self.root).find("p:" + tag, NS)
        self.assertIsNotNone(el, tag)
        self.assertTrue((el.text or "").strip(), tag)
        return el

    def test_format_and_required(self):
        self.assertEqual(self.root.tag, "{%s}package" % NS["p"])
        self.assertEqual(self.root.get("format"), "1")
        for tag in ("name", "description", "version", "date", "maintainer", "license", "icon", "freecadmin"):
            self.one(tag)
        self.assertRegex(self.one("version").text, r"^\d+\.\d+\.\d+$")
        self.assertRegex(self.one("date").text, r"^\d{4}-\d{2}-\d{2}$")
        self.assertRegex(self.one("freecadmin").text, r"^\d+\.\d+(\.\d+)?$")

    def test_version_matches_package(self):
        src = open(os.path.join(ROOT, PKG, "__init__.py"), encoding="utf-8").read()
        m = re.search(r'^VERSION\s*=\s*"([^"]+)"', src, re.M)
        self.assertIsNotNone(m)
        self.assertEqual(m.group(1), self.one("version").text.strip())

    def test_maintainer_and_licence(self):
        self.one("maintainer")
        lic = self.one("license")
        self.assertEqual(lic.text.strip(), MAIN)
        self.assertTrue(os.path.exists(os.path.join(ROOT, lic.get("file"))))

    def test_urls(self):
        urls = {u.get("type"): (u.text or "").strip() for u in self.root.findall("p:url", NS)}
        for kind in URLS:
            self.assertTrue(urls.get(kind, "").startswith("https://"), kind)

    def test_icons_exist(self):
        self.assertTrue(os.path.isfile(os.path.join(ROOT, self.one("icon").text.strip())))
        for wb in self.root.findall("p:content/p:workbench", NS):
            self.assertTrue(os.path.isfile(os.path.join(ROOT, self.one("icon", wb).text.strip())))

    def test_workbench_matches_initgui(self):
        wbs = self.root.findall("p:content/p:workbench", NS)
        self.assertEqual(len(wbs), 1)
        initgui = open(os.path.join(ROOT, "InitGui.py"), encoding="utf-8").read()
        for wb in wbs:
            cls = self.one("classname", wb).text.strip()
            # InitGui.py only calls workbench.add(); the class lives in the
            # module (FreeCAD's exec() hides InitGui's own names from it).
            built = open(os.path.join(ROOT, "texturizer", "workbench.py"), encoding="utf-8").read()
            self.assertRegex(built, r"(?m)^    class %s\(Gui\.Workbench\)" % re.escape(cls))
            self.assertRegex(initgui, r"(?m)^_texturizer_workbench\.add\(FreeCADGui\)")
            sub = self.one("subdirectory", wb).text.strip()
            self.assertTrue(os.path.isfile(os.path.join(ROOT, sub, "InitGui.py")))
            self.one("name", wb)

    def test_tags(self):
        tags = [t.text.strip() for t in self.root.findall("p:tag", NS)]
        self.assertGreaterEqual(len(tags), 3)
        self.assertEqual(len(tags), len(set(tags)))

    def test_credits_bumpmesh(self):
        desc = self.one("description").text
        self.assertIn("Stefan Hermann", desc)
        self.assertIn("CNC Kitchen", desc)


if __name__ == "__main__":
    unittest.main()
