"""Regression tests for bookmarks.py. Run: python3 -m unittest discover tests"""

import json
import os
import re
import shlex
import sqlite3
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_TMP = tempfile.mkdtemp(prefix="bookmark-launcher-test-")
os.environ["BOOKMARKS_CACHE_DIR"] = os.path.join(_TMP, "cache")
os.environ["BOOKMARKS_MENU_FILE"] = os.path.join(_TMP, "omarchy-menu.jsonc")
sys.path.insert(0, ROOT)

import bookmarks as bm  # noqa: E402

S = bm.Strings("en")


def strip_jsonc(raw):
    """Port of stripJsonc() in Omarchy's shell/plugins/menu/MenuModel.js."""
    raw = re.sub(r"^[ \t\f\v\r  ]*//[^\n]*(\n|$)", "", raw, flags=re.M)
    return re.sub(r",(\s*[}\]])", r"\1", raw)


def parse_menu(text):
    return json.loads(strip_jsonc(text))


def chromium_node(name, children=None, url=None):
    if url is not None:
        return {"type": "url", "name": name, "url": url}
    return {"type": "folder", "name": name, "children": children or []}


def chromium_file(path, bar, other=()):
    data = {"roots": {
        "bookmark_bar": chromium_node("Bookmarks bar", list(bar)),
        "other": chromium_node("Other", list(other)),
        "synced": chromium_node("Mobile", []),
    }}
    with open(path, "w") as f:
        json.dump(data, f)


def firefox_db(path, entries):
    """entries: list of (root_guid, title, url)."""
    con = sqlite3.connect(path)
    con.executescript("""
        CREATE TABLE moz_places (id INTEGER PRIMARY KEY, url TEXT);
        CREATE TABLE moz_bookmarks (id INTEGER PRIMARY KEY, type INTEGER, fk INTEGER,
            parent INTEGER, position INTEGER, title TEXT, guid TEXT);
        INSERT INTO moz_bookmarks VALUES (1, 2, NULL, 0, 0, '', 'root________');
        INSERT INTO moz_bookmarks VALUES (2, 2, NULL, 1, 0, 'menu', 'menu________');
        INSERT INTO moz_bookmarks VALUES (3, 2, NULL, 1, 1, 'toolbar', 'toolbar_____');
        INSERT INTO moz_bookmarks VALUES (4, 2, NULL, 1, 2, 'tags', 'tags________');
        INSERT INTO moz_bookmarks VALUES (5, 2, NULL, 1, 3, 'unfiled', 'unfiled_____');
    """)
    parents = {"menu________": 2, "toolbar_____": 3, "tags________": 4, "unfiled_____": 5}
    for i, (root, title, url) in enumerate(entries):
        pid = 100 + i
        con.execute("INSERT INTO moz_places VALUES (?, ?)", (pid, url))
        con.execute("INSERT INTO moz_bookmarks VALUES (?, 1, ?, ?, ?, ?, ?)",
                    (pid, pid, parents[root], i, title, f"guid{i:08d}"))
    con.commit()
    con.close()


class CleanUrl(unittest.TestCase):
    def test_accepts_http_and_https(self):
        self.assertEqual(bm.clean_url("https://example.com/a?b=c#d"), "https://example.com/a?b=c#d")
        self.assertEqual(bm.clean_url("http://example.com"), "http://example.com")

    def test_rejects_other_schemes_and_junk(self):
        for url in ["javascript:alert(1)", "JAVASCRIPT:alert(1)", "file:///etc/passwd",
                    "data:text/html,<script>", "chrome://settings", "place:sort=8",
                    "https://", "https://exa\nmple.com", "https://example.com/\x7f",
                    "https://example.com/" + "a" * 3000, "", None, 42]:
            self.assertIsNone(bm.clean_url(url), url)


class JsonEscaping(unittest.TestCase):
    HOSTILE = [
        'a,}b', 'a, ]b', '}, "x": {"action": "rm -rf ~"', '// not a comment',
        'line // break', 'para x', 'quote " and \\ backslash', 'tab\tnew\nline',
        '<img src="https://example.invalid/t">', 'Türkçe ğüşıöç 日本語 🙂',
    ]

    def test_values_survive_menu_jsonc_stripper(self):
        for value in self.HOSTILE:
            text = "{\n" + bm.row("bookmarks.x", {"label": value, "description": value}) + "\n}"
            parsed = parse_menu(text)
            self.assertEqual(parsed["bookmarks.x"]["label"], value)
            self.assertEqual(list(parsed), ["bookmarks.x"])

    def test_output_is_ascii_with_no_raw_brackets_inside_strings(self):
        out = bm.js('x}] ')
        self.assertTrue(out.isascii())
        self.assertNotIn("}", out)
        self.assertNotIn("]", out)

    def test_markup_title_stays_literal(self):
        title = '<img src="https://example.invalid/t">'
        self.assertEqual(bm.clean_label(title, "x"), title)


class ShellQuoting(unittest.TestCase):
    def test_action_argument_is_one_literal_word(self):
        for url in ["https://e.com/'; rm -rf ~; '", "https://e.com/$(id)", "https://e.com/`id`",
                    "https://e.com/a b\\c\"d", "https://e.com/?a=1&b=2|x>y"]:
            cmd = f"exec python3 {bm.shq('/p/bookmarks.py')} open {bm.shq('chrome')} {bm.shq('')} {bm.shq(url)}"
            self.assertEqual(shlex.split(cmd), ["exec", "python3", "/p/bookmarks.py", "open", "chrome", "", url])

    def test_open_refuses_bad_input(self):
        self.assertEqual(bm.cmd_open("chrome", "Default", "javascript:alert(1)"), 2)
        self.assertEqual(bm.cmd_open("not-a-browser", "", "https://example.com"), 2)


class Splice(unittest.TestCase):
    USER = '{\n  // my stuff\n  "personal": {"icon":"x","label":"Personal"},\n  "personal.a": {"label":"A","action":"true"}\n}\n'

    def block(self, label="Bookmarks"):
        return [bm.BEGIN, bm.row("bookmarks", {"label": label}), bm.END]

    def test_insert_replace_remove_roundtrip(self):
        inserted = bm.splice(self.USER, self.block())
        parsed = parse_menu(inserted)
        self.assertEqual(parsed["bookmarks"]["label"], "Bookmarks")
        self.assertEqual(parsed["personal.a"]["label"], "A")

        replaced = bm.splice(inserted, self.block("Yer İmleri"))
        self.assertEqual(replaced.count(bm.BEGIN), 1)
        self.assertEqual(parse_menu(replaced)["bookmarks"]["label"], "Yer İmleri")

        self.assertEqual(bm.splice(replaced, []), self.USER)

    def test_missing_file_and_empty_object(self):
        self.assertIn("bookmarks", parse_menu(bm.splice(None, self.block())))
        self.assertIn("bookmarks", parse_menu(bm.splice("{\n}\n", self.block())))
        self.assertIsNone(bm.splice(None, []))

    def test_refuses_unrecognized_layout(self):
        with self.assertRaises(RuntimeError):
            bm.splice('{"a": {"label": "x"}}', self.block())

    def test_begin_without_end_is_not_truncated(self):
        broken = '{\n' + bm.BEGIN + '\n  "x": {"label":"X"},\n}\n'
        out = bm.splice(broken, self.block())
        self.assertIn('"x": {"label":"X"}', out)


class Render(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(dir=_TMP)

    def chromium_source(self):
        prof = os.path.join(self.tmp, "Default")
        os.makedirs(prof)
        chromium_file(os.path.join(prof, "Bookmarks"), [
            chromium_node("Example", url="https://example.com/"),
            chromium_node("Work", [chromium_node("Docs", url="https://docs.example.com/")]),
            chromium_node("Empty", []),
            chromium_node("Evil", url="javascript:alert(1)"),
        ], other=[chromium_node("Later", url="https://later.example.com/")])
        chromium_file(os.path.join(prof, "AccountBookmarks"), [
            chromium_node("Example dup", url="https://example.com/"),
            chromium_node("Synced", url="https://synced.example.com/"),
        ])
        return {"browser": bm.BY_KEY["chrome"], "profile": "Default", "profile_label": "Person 1",
                "files": [os.path.join(prof, "Bookmarks"), os.path.join(prof, "AccountBookmarks")]}

    def firefox_source(self):
        db = os.path.join(self.tmp, "places.sqlite")
        firefox_db(db, [
            ("toolbar_____", "Toolbar site", "https://toolbar.example.com/"),
            ("menu________", "Menu site", "https://menu.example.com/"),
            ("tags________", "tag", "https://tag.example.com/"),
            ("unfiled_____", "Smart", "place:sort=8"),
        ])
        return {"browser": bm.BY_KEY["firefox"], "profile": "", "profile_label": "default-release", "files": [db]}

    def test_render_both_browsers(self):
        lines, total = bm.render([self.chromium_source(), self.firefox_source()], S)
        menu = parse_menu("{\n" + "\n".join(lines) + "\n}")
        labels = {v["label"]: (k, v) for k, v in menu.items()}

        # Chromium: merged, de-duplicated, empty folder and javascript: dropped.
        for name in ["Example", "Docs", "Later", "Synced", "Work", "Other bookmarks"]:
            self.assertIn(name, labels)
        for name in ["Example dup", "Empty", "Evil"]:
            self.assertNotIn(name, labels)
        # Firefox: toolbar flattened, menu folder kept, tags and place: dropped.
        for name in ["Toolbar site", "Menu site", "Bookmarks menu"]:
            self.assertIn(name, labels)
        self.assertNotIn("tag", labels)
        self.assertNotIn("Smart", labels)
        self.assertEqual(total, 6)

        # Every parent referenced by a dotted id exists.
        for key in menu:
            parent = key.rsplit(".", 1)[0]
            if "." in key:
                self.assertIn(parent, menu, key)

        # Actions name the right browser and profile, and the URL is searchable.
        _, docs = labels["Docs"]
        argv = shlex.split(docs["action"])
        self.assertEqual(argv[-4:], ["open", "chrome", "Default", "https://docs.example.com/"])
        self.assertIn("https://docs.example.com/", docs["description"])
        _, ff = labels["Toolbar site"]
        self.assertEqual(shlex.split(ff["action"])[-4:], ["open", "firefox", "", "https://toolbar.example.com/"])

    def test_render_empty(self):
        lines, total = bm.render([], S)
        menu = parse_menu("{\n" + "\n".join(lines) + "\n}")
        self.assertEqual(total, 0)
        self.assertIn("bookmarks.none", menu)

    def test_translations(self):
        tr = bm.Strings("tr")
        self.assertEqual(tr.t("menu.title"), "Yer İmleri")
        self.assertEqual(tr.tn("menu.count", 3), "3 yer imi")
        self.assertEqual(bm.Strings("tr-XX").t("menu.title"), "Yer İmleri")
        self.assertEqual(bm.Strings("zz").t("menu.title"), "Bookmarks")
        self.assertEqual(S.tn("menu.count", 1), "1 bookmark")


class Cleanup(unittest.TestCase):
    """The inline cleanup in Service.qml must remove exactly the managed block."""

    def test_service_cleanup_matches_markers(self):
        qml = open(os.path.join(ROOT, "Service.qml"), encoding="utf-8").read()
        self.assertIn(f'pluginId: "{bm.PLUGIN_ID}"', qml)
        body = re.search(r'"python3", "-I", "-c",\n(.*?),\n\s*root\.menuFile', qml, re.S).group(1)
        code = "".join(json.loads(part) for part in re.findall(r'"(?:[^"\\]|\\.)*"', body))

        user = Splice.USER
        menu = os.path.join(_TMP, "cleanup.jsonc")
        with open(menu, "w") as f:
            f.write(bm.splice(user, [bm.BEGIN, bm.row("bookmarks", {"label": "B"}), bm.END]))
        cache = os.path.join(_TMP, "cleanup-cache")
        import subprocess
        subprocess.run([sys.executable, "-I", "-c", code, menu, bm.PLUGIN_ID, cache], check=True)
        self.assertEqual(open(menu).read(), user)


if __name__ == "__main__":
    unittest.main()
