#!/usr/bin/env python3
"""Browser bookmarks for the Omarchy launcher.

  bookmarks.py sync [--force]        regenerate the launcher rows if anything changed
  bookmarks.py open <key> <profile> <url>   open a bookmark in the browser it came from
  bookmarks.py remove                drop the generated rows from the launcher

Bookmarks are read from Chromium-family `Bookmarks`/`AccountBookmarks` files and
Firefox-family `places.sqlite` databases, then written as a managed block of
static rows into ~/.config/omarchy/extensions/omarchy-menu.jsonc, which the
launcher hot-reloads and searches. Every value taken from a bookmark is treated
as untrusted: only http(s) URLs are kept, every string is JSON-escaped (with
the characters the menu's JSONC stripper would touch escaped too), and the URL
reaches the launch command only as a single-quoted shell argument that is then
handed to the browser as a plain argv element.
"""

import configparser
import fcntl
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from urllib.parse import urlsplit

SELF = os.path.realpath(__file__)
PLUGIN_DIR = os.path.dirname(SELF)


def _plugin_id():
    try:
        with open(os.path.join(PLUGIN_DIR, "manifest.json"), encoding="utf-8") as f:
            pid = json.load(f).get("id", "")
        if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", pid or ""):
            return pid
    except (OSError, ValueError):
        pass
    return "xmsadik.bookmark-launcher"


PLUGIN_ID = _plugin_id()
HOME = os.path.expanduser("~")
CONFIG = os.environ.get("XDG_CONFIG_HOME") or os.path.join(HOME, ".config")
MENU_FILE = os.environ.get("BOOKMARKS_MENU_FILE") or os.path.join(CONFIG, "omarchy", "extensions", "omarchy-menu.jsonc")
CACHE_DIR = os.environ.get("BOOKMARKS_CACHE_DIR") or os.path.join(
    os.environ.get("XDG_CACHE_HOME") or os.path.join(HOME, ".cache"), PLUGIN_ID)
SIGNATURE_FILE = os.path.join(CACHE_DIR, "signature")
LOCK_FILE = os.path.join(CACHE_DIR, "lock")
LANGUAGE_FILE = os.path.join(CONFIG, "omarchy", "language")

FORMAT_VERSION = "3"
# Service.qml deletes this exact block on unload; keep the two in sync.
BEGIN = f"  // >>> {PLUGIN_ID}: generated automatically, edits here are overwritten"
END = f"  // <<< {PLUGIN_ID}"
MENU_ID = "bookmarks"

MAX_URL = 2048
MAX_LABEL = 160
MAX_PER_PROFILE = 3000
MAX_DEPTH = 12

ICON_ROOT = "\U000f00c0"      # nf-md-bookmark
ICON_BOOKMARK = "\U000f00c3"  # nf-md-bookmark_outline
ICON_FOLDER = ""        # nf-fa-folder
ICON_REFRESH = "\U000f0450"   # nf-md-refresh
ICON_GLOBE = "\U000f059f"     # nf-md-web
ICON_CHROME = ""
ICON_FIREFOX = ""
ICON_EDGE = ""


def cfg(*parts):
    return os.path.join(CONFIG, *parts)


def var(app, *parts):
    return os.path.join(HOME, ".var", "app", app, *parts)


# key: stable id used in menu ids and passed back to `open`.
# kind: chromium | firefox.  roots: candidate data dirs.
# run: native binary name, or "flatpak:<app-id>".  wm: window class regex for focusing.
BROWSERS = [
    {"key": "chrome", "label": "Chrome", "icon": ICON_CHROME, "kind": "chromium", "roots": [cfg("google-chrome")], "run": "google-chrome-stable", "wm": "^google-chrome"},
    {"key": "chrome-beta", "label": "Chrome Beta", "icon": ICON_CHROME, "kind": "chromium", "roots": [cfg("google-chrome-beta")], "run": "google-chrome-beta", "wm": "^google-chrome-beta"},
    {"key": "chrome-dev", "label": "Chrome Dev", "icon": ICON_CHROME, "kind": "chromium", "roots": [cfg("google-chrome-unstable")], "run": "google-chrome-unstable", "wm": "^google-chrome-unstable"},
    {"key": "chromium", "label": "Chromium", "icon": ICON_CHROME, "kind": "chromium", "roots": [cfg("chromium")], "run": "chromium", "wm": "^chromium"},
    {"key": "brave", "label": "Brave", "icon": ICON_GLOBE, "kind": "chromium", "roots": [cfg("BraveSoftware", "Brave-Browser")], "run": "brave", "wm": "^brave-browser"},
    {"key": "brave-beta", "label": "Brave Beta", "icon": ICON_GLOBE, "kind": "chromium", "roots": [cfg("BraveSoftware", "Brave-Browser-Beta")], "run": "brave-beta", "wm": "^brave-browser-beta"},
    {"key": "brave-nightly", "label": "Brave Nightly", "icon": ICON_GLOBE, "kind": "chromium", "roots": [cfg("BraveSoftware", "Brave-Browser-Nightly")], "run": "brave-nightly", "wm": "^brave-browser-nightly"},
    {"key": "brave-origin", "label": "Brave Origin", "icon": ICON_GLOBE, "kind": "chromium", "roots": [cfg("BraveSoftware", "Brave-Origin")], "run": "brave-origin", "wm": "^brave-origin"},
    {"key": "edge", "label": "Edge", "icon": ICON_EDGE, "kind": "chromium", "roots": [cfg("microsoft-edge")], "run": "microsoft-edge-stable", "wm": "^microsoft-edge"},
    {"key": "edge-beta", "label": "Edge Beta", "icon": ICON_EDGE, "kind": "chromium", "roots": [cfg("microsoft-edge-beta")], "run": "microsoft-edge-beta", "wm": "^microsoft-edge-beta"},
    {"key": "edge-dev", "label": "Edge Dev", "icon": ICON_EDGE, "kind": "chromium", "roots": [cfg("microsoft-edge-dev")], "run": "microsoft-edge-dev", "wm": "^microsoft-edge-dev"},
    {"key": "vivaldi", "label": "Vivaldi", "icon": ICON_GLOBE, "kind": "chromium", "roots": [cfg("vivaldi")], "run": "vivaldi-stable", "wm": "^vivaldi"},
    {"key": "helium", "label": "Helium", "icon": ICON_GLOBE, "kind": "chromium", "roots": [cfg("net.imput.helium")], "run": "helium-browser", "wm": "^helium"},
    {"key": "thorium", "label": "Thorium", "icon": ICON_GLOBE, "kind": "chromium", "roots": [cfg("thorium")], "run": "thorium-browser", "wm": "^thorium"},
    {"key": "chrome-flatpak", "label": "Chrome (Flatpak)", "icon": ICON_CHROME, "kind": "chromium", "roots": [var("com.google.Chrome", "config", "google-chrome")], "run": "flatpak:com.google.Chrome", "wm": "^google-chrome"},
    {"key": "chromium-flatpak", "label": "Chromium (Flatpak)", "icon": ICON_CHROME, "kind": "chromium", "roots": [var("org.chromium.Chromium", "config", "chromium")], "run": "flatpak:org.chromium.Chromium", "wm": "^chromium"},
    {"key": "brave-flatpak", "label": "Brave (Flatpak)", "icon": ICON_GLOBE, "kind": "chromium", "roots": [var("com.brave.Browser", "config", "BraveSoftware", "Brave-Browser")], "run": "flatpak:com.brave.Browser", "wm": "^brave-browser"},
    {"key": "edge-flatpak", "label": "Edge (Flatpak)", "icon": ICON_EDGE, "kind": "chromium", "roots": [var("com.microsoft.Edge", "config", "microsoft-edge")], "run": "flatpak:com.microsoft.Edge", "wm": "^microsoft-edge"},
    {"key": "firefox", "label": "Firefox", "icon": ICON_FIREFOX, "kind": "firefox", "roots": [cfg("mozilla", "firefox"), os.path.join(HOME, ".mozilla", "firefox")], "run": "firefox", "wm": "^firefox"},
    {"key": "zen", "label": "Zen", "icon": ICON_GLOBE, "kind": "firefox", "roots": [cfg("zen"), os.path.join(HOME, ".zen")], "run": "zen-browser", "wm": "^zen"},
    {"key": "librewolf", "label": "LibreWolf", "icon": ICON_GLOBE, "kind": "firefox", "roots": [cfg("librewolf", "librewolf"), cfg("librewolf"), os.path.join(HOME, ".librewolf")], "run": "librewolf", "wm": "^librewolf"},
    {"key": "floorp", "label": "Floorp", "icon": ICON_GLOBE, "kind": "firefox", "roots": [cfg("floorp"), os.path.join(HOME, ".floorp")], "run": "floorp", "wm": "^floorp"},
    {"key": "waterfox", "label": "Waterfox", "icon": ICON_GLOBE, "kind": "firefox", "roots": [cfg("waterfox"), os.path.join(HOME, ".waterfox")], "run": "waterfox", "wm": "^waterfox"},
    {"key": "firefox-flatpak", "label": "Firefox (Flatpak)", "icon": ICON_FIREFOX, "kind": "firefox", "roots": [var("org.mozilla.firefox", "config", "mozilla", "firefox"), var("org.mozilla.firefox", ".mozilla", "firefox")], "run": "flatpak:org.mozilla.firefox", "wm": "^firefox"},
    {"key": "zen-flatpak", "label": "Zen (Flatpak)", "icon": ICON_GLOBE, "kind": "firefox", "roots": [var("app.zen_browser.zen", ".zen")], "run": "flatpak:app.zen_browser.zen", "wm": "^zen"},
    {"key": "librewolf-flatpak", "label": "LibreWolf (Flatpak)", "icon": ICON_GLOBE, "kind": "firefox", "roots": [var("io.gitlab.librewolf-community", ".librewolf")], "run": "flatpak:io.gitlab.librewolf-community", "wm": "^librewolf"},
]
BY_KEY = {b["key"]: b for b in BROWSERS}


# ---------------------------------------------------------------- strings

def current_language():
    try:
        with open(LANGUAGE_FILE, encoding="utf-8") as f:
            code = f.readline().strip()
        if re.fullmatch(r"[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8})?", code):
            return code
    except OSError:
        pass
    return "en"


def load_strings(lang):
    """Merge en <- base language <- full language, like Commons.Translations."""
    chain = ["en"]
    for code in (lang.split("-")[0], lang):
        if code not in chain:
            chain.append(code)
    strings = {}
    for code in chain:
        data = read_json(os.path.join(PLUGIN_DIR, "i18n", f"{code}.json"))
        if isinstance(data, dict):
            strings.update(data)
    return strings


class Strings:
    def __init__(self, lang="en"):
        self.data = load_strings(lang)

    def t(self, key, *args):
        value = self.data.get(key, key)
        if isinstance(value, dict):
            value = value.get("other", key)
        return self._fill(str(value), args)

    def tn(self, key, count, *args):
        value = self.data.get(key, key)
        if isinstance(value, dict):
            value = value.get("one" if count == 1 else "other", value.get("other", key))
        return self._fill(str(value), (count,) + args)

    @staticmethod
    def _fill(text, args):
        for i, a in enumerate(args, 1):
            text = text.replace(f"%{i}", str(a))
        return text


# ---------------------------------------------------------------- discovery

def installed(browser):
    run = browser["run"]
    if run.startswith("flatpak:"):
        app = run.split(":", 1)[1]
        return shutil.which("flatpak") is not None and any(
            os.path.isdir(os.path.join(base, app))
            for base in ("/var/lib/flatpak/app", os.path.join(HOME, ".local/share/flatpak/app"))
        )
    return shutil.which(run) is not None


def read_json(path):
    try:
        with open(path, "rb") as f:
            if os.fstat(f.fileno()).st_size > 64 * 1024 * 1024:
                return None
            return json.loads(f.read().decode("utf-8", "replace"))
    except (OSError, ValueError):
        return None


def chromium_profiles(root):
    """[(profile_dir, display_name, [bookmark files])] for one Chromium data dir."""
    names = {}
    state = read_json(os.path.join(root, "Local State"))
    try:
        cache = state["profile"]["info_cache"]
        for d, info in cache.items():
            names[d] = str(info.get("name") or d)
    except (TypeError, KeyError, AttributeError):
        pass
    try:
        dirs = [d for d in os.listdir(root) if d == "Default" or d.startswith("Profile ")]
    except OSError:
        return []
    out = []
    for d in sorted(dirs, key=lambda x: (x != "Default", x)):
        files = [os.path.join(root, d, n) for n in ("Bookmarks", "AccountBookmarks")]
        files = [f for f in files if os.path.isfile(f)]
        if files:
            out.append((d, names.get(d, d), files))
    return out


def firefox_profiles(root):
    """[(profile_name_or_empty, display_name, [places.sqlite, wal?])]."""
    ini = configparser.ConfigParser(interpolation=None)
    try:
        if not ini.read(os.path.join(root, "profiles.ini"), encoding="utf-8"):
            return []
    except (configparser.Error, OSError):
        return []
    profiles = []
    for section in ini.sections():
        if not section.startswith("Profile"):
            continue
        path = ini.get(section, "Path", fallback="")
        if not path:
            continue
        relative = ini.get(section, "IsRelative", fallback="1") == "1"
        full = os.path.join(root, path) if relative else path
        places = os.path.join(full, "places.sqlite")
        if os.path.isfile(places):
            name = ini.get(section, "Name", fallback=os.path.basename(path))
            profiles.append((name, places))
    multiple = len(profiles) > 1
    out = []
    for name, places in profiles:
        files = [places] + [places + s for s in ("-wal",) if os.path.isfile(places + s)]
        out.append((name if multiple else "", name, files))
    return out


def discover():
    """List of sources: dict(browser, profile, profile_label, files)."""
    sources = []
    for b in BROWSERS:
        if not installed(b):
            continue
        seen_roots = set()
        for root in b["roots"]:
            real = os.path.realpath(root)
            if real in seen_roots or not os.path.isdir(root):
                continue
            seen_roots.add(real)
            found = chromium_profiles(root) if b["kind"] == "chromium" else firefox_profiles(root)
            for profile, label, files in found:
                sources.append({"browser": b, "profile": profile, "profile_label": label, "files": files})
            if found:
                break  # first root with profiles wins (e.g. XDG vs legacy Firefox dir)
    return sources


def signature(sources, lang):
    h = hashlib.sha256(FORMAT_VERSION.encode())
    h.update(lang.encode())
    for f in (default_menu_file(), menu_catalog_file(lang)):
        try:
            st = os.stat(f)
            h.update(f"{f}|{st.st_mtime_ns}|{st.st_size}".encode())
        except (OSError, TypeError):
            pass
    h.update(SELF.encode())
    for s in sources:
        h.update(f"{s['browser']['key']}|{s['profile']}|{s['profile_label']}".encode())
        for f in s["files"]:
            try:
                st = os.stat(f)
                h.update(f"{f}|{st.st_mtime_ns}|{st.st_size}".encode())
            except OSError:
                pass
    return h.hexdigest()


# ---------------------------------------------------------------- reading

def clean_url(url):
    if not isinstance(url, str) or not url or len(url) > MAX_URL:
        return None
    if any(ord(c) < 0x20 or ord(c) == 0x7F for c in url):
        return None
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    if parts.scheme.lower() not in ("http", "https") or not parts.netloc:
        return None
    return url


def clean_label(text, fallback):
    text = text if isinstance(text, str) else ""
    text = re.sub(r"[\x00-\x1f\x7f  ]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        text = fallback
    if len(text) > MAX_LABEL:
        text = text[: MAX_LABEL - 1] + "…"
    return text


# A tree node: {"title": str, "children": [node], "url": str|None}

def chromium_tree(files, S):
    """Merge Bookmarks + AccountBookmarks into one tree of top-level groups."""
    groups = {"bookmark_bar": [], "other": [], "synced": []}
    for f in files:
        data = read_json(f)
        try:
            roots = data["roots"]
        except (TypeError, KeyError):
            continue
        for key in groups:
            node = roots.get(key) if isinstance(roots, dict) else None
            if isinstance(node, dict):
                groups[key].extend(node.get("children") or [])

    def convert(node, depth):
        if not isinstance(node, dict) or depth > MAX_DEPTH:
            return None
        if node.get("type") == "url":
            return {"title": node.get("name"), "url": node.get("url"), "children": []}
        if node.get("type") == "folder":
            kids = [c for c in (convert(k, depth + 1) for k in node.get("children") or []) if c]
            return {"title": node.get("name"), "url": None, "children": kids}
        return None

    top = [c for c in (convert(n, 1) for n in groups["bookmark_bar"]) if c]
    for key, title in (("other", S.t("folder.other")), ("synced", S.t("folder.mobile"))):
        kids = [c for c in (convert(n, 1) for n in groups[key]) if c]
        if kids:
            top.append({"title": title, "url": None, "children": kids})
    return top


def firefox_tree(files, S):
    places = files[0]
    os.makedirs(CACHE_DIR, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="ff-", dir=CACHE_DIR) as tmp:
        copy = os.path.join(tmp, "places.sqlite")
        try:
            shutil.copy2(places, copy)
            if os.path.isfile(places + "-wal"):
                shutil.copy2(places + "-wal", copy + "-wal")
        except OSError:
            return []
        try:
            con = sqlite3.connect(copy)
            rows = con.execute(
                "SELECT b.id, b.type, b.parent, b.title, b.guid, p.url "
                "FROM moz_bookmarks b LEFT JOIN moz_places p ON b.fk = p.id "
                "ORDER BY b.parent, b.position"
            ).fetchall()
            con.close()
        except sqlite3.Error:
            return []

    by_parent, guid_id = {}, {}
    for rid, rtype, parent, title, guid, url in rows:
        by_parent.setdefault(parent, []).append((rid, rtype, title, url))
        guid_id[guid] = rid

    def build(parent_id, depth):
        out = []
        if depth > MAX_DEPTH:
            return out
        for rid, rtype, title, url in by_parent.get(parent_id, []):
            if rtype == 1:
                out.append({"title": title, "url": url, "children": []})
            elif rtype == 2:
                out.append({"title": title, "url": None, "children": build(rid, depth + 1)})
        return out

    top = build(guid_id["toolbar_____"], 1) if "toolbar_____" in guid_id else []
    for guid, title in (("menu________", S.t("folder.menu")), ("unfiled_____", S.t("folder.other")), ("mobile______", S.t("folder.mobile"))):
        if guid in guid_id:
            kids = build(guid_id[guid], 1)
            if kids:
                top.append({"title": title, "url": None, "children": kids})
    return top


# ---------------------------------------------------------------- rendering

def js(value):
    """JSON string literal that survives the menu's JSONC pre-processing.

    The menu strips `,` before `}`/`]` with a regex over the raw text and drops
    lines starting with `//`, so escape brackets and every non-ASCII character
    (including U+2028/9, which a JS multiline regex treats as a line break).
    """
    return json.dumps(value, ensure_ascii=True).replace("}", "\\u007d").replace("]", "\\u005d")


def shq(value):
    return "'" + value.replace("'", "'\\''") + "'"


def js_value(value):
    if isinstance(value, (list, tuple)):
        return "[" + ",".join(js(v) for v in value) + "]"
    return js(value)


def row(item_id, fields):
    body = ",".join(f"{js(k)}:{js_value(v)}" for k, v in fields.items())
    return f"  {js(item_id)}: {{{body}}},"


# ---------------------------------------------------------------- placement
#
# The launcher lists rows in the order their ids first appear, defaults first,
# so a new root row always lands after Omarchy's own (below About and System).
# To show Bookmarks just above About, hide the default root rows from About on
# (`when: false` keeps them routable and their children searchable) and add a
# copy of each right after Bookmarks: an action keeps its action, a submenu
# becomes a link to the original. Icons, actions and the translated labels are
# read from Omarchy's own files at sync time, so they follow Omarchy updates.

PLACE_BEFORE = "about"


def omarchy_path():
    return os.environ.get("OMARCHY_PATH") or "/usr/share/omarchy"


def default_menu_file():
    return os.path.join(omarchy_path(), "default", "omarchy", "omarchy-menu.jsonc")


def menu_catalog_file(lang):
    for code in (lang, lang.split("-")[0], "en"):
        path = os.path.join(omarchy_path(), "default", "i18n", code, "menu.json")
        if os.path.isfile(path):
            return path
    return None


def strip_jsonc(raw):
    """Port of stripJsonc() in Omarchy's shell/plugins/menu/MenuModel.js."""
    raw = re.sub(r"^[ \t\f\v\r\u2028\u2029]*//[^\n]*(\n|$)", "", raw, flags=re.M)
    return re.sub(r",(\s*[}\]])", r"\1", raw)


def moved_rows(lang):
    """Rows that put Bookmarks before PLACE_BEFORE, or [] if that can't be done safely."""
    try:
        with open(default_menu_file(), encoding="utf-8") as f:
            defaults = json.loads(strip_jsonc(f.read()))
    except (OSError, ValueError):
        return []
    if not isinstance(defaults, dict):
        return []
    roots = [k for k, v in defaults.items()
             if isinstance(v, dict) and "." not in k and v.get("parent", "root") == "root"]
    if PLACE_BEFORE not in roots:
        return []
    catalog = read_json(menu_catalog_file(lang) or "") or {}

    hide, copies = [], []
    for item_id in roots[roots.index(PLACE_BEFORE):]:
        if item_id == MENU_ID:
            continue
        entry = defaults[item_id]
        translated = catalog.get(item_id) if isinstance(catalog.get(item_id), dict) else {}
        label = translated.get("label") or entry.get("label") or item_id
        copy = {"icon": entry.get("icon", ""), "label": label}
        if entry.get("iconFont"):
            copy["iconFont"] = entry["iconFont"]
        if entry.get("action"):
            copy["action"] = entry["action"]
            for key in ("when", "checked", "disabled"):
                if entry.get(key):
                    copy[key] = entry[key]
        else:
            copy["target"] = entry.get("target") or item_id
        hide.append(row(item_id, {"when": "false"}))
        copies.append(row(f"{MENU_ID}-then-{item_id}", copy))
    return hide + copies


def render(sources, S, lang="en"):
    lines = [BEGIN]
    lines.append(row(MENU_ID, {"icon": ICON_ROOT, "label": S.t("menu.title"), "aliases": ["bookmarks"], "description": S.t("menu.description")}))

    per_browser = {}
    for s in sources:
        per_browser.setdefault(s["browser"]["key"], []).append(s)

    total = 0
    used_ids = set()
    for s in sources:
        b = s["browser"]
        try:
            tree = chromium_tree(s["files"], S) if b["kind"] == "chromium" else firefox_tree(s["files"], S)
        except Exception as e:  # one broken profile must not drop the others
            print(f"bookmarks: skipping {b['key']} {s['profile_label']}: {e}", file=sys.stderr)
            continue

        slug = re.sub(r"[^a-z0-9]+", "-", s["profile_label"].lower()).strip("-") or "profile"
        node_id = f"{MENU_ID}.{b['key']}-{slug}"
        n = 2
        while node_id in used_ids:
            node_id = f"{MENU_ID}.{b['key']}-{slug}-{n}"
            n += 1
        used_ids.add(node_id)

        label = b["label"]
        if len(per_browser[b["key"]]) > 1:
            label = f"{label} — {clean_label(s['profile_label'], S.t('profile'))}"

        body, count = [], [0]
        seen_urls = set()

        def emit(nodes, parent_id, path):
            emitted = 0
            for i, node in enumerate(nodes):
                if count[0] >= MAX_PER_PROFILE:
                    return emitted
                if node["url"] is None:
                    fid = f"{parent_id}.f{i}"
                    title = clean_label(node["title"], S.t("folder"))
                    mark = len(body)
                    body.append(row(fid, {"icon": ICON_FOLDER, "label": title}))
                    if emit(node["children"], fid, path + [title]) == 0:
                        del body[mark:]  # drop empty folders
                    else:
                        emitted += 1
                    continue
                url = clean_url(node["url"])
                if not url or url in seen_urls:
                    continue
                seen_urls.add(url)
                action = f"exec python3 -I {shq(SELF)} open {shq(b['key'])} {shq(s['profile'])} {shq(url)}"
                where = " / ".join([label] + path)
                body.append(row(f"{parent_id}.b{i}", {
                    "icon": ICON_BOOKMARK,
                    "label": clean_label(node["title"], url),
                    "description": f"{url}  ·  {where}",
                    "action": action,
                }))
                count[0] += 1
                emitted += 1
            return emitted

        emit(tree, node_id, [])
        if count[0]:
            lines.append(row(node_id, {"icon": b["icon"], "label": label, "description": S.tn("menu.count", count[0])}))
            lines.extend(body)
            total += count[0]

    if total == 0:
        lines.append(row(f"{MENU_ID}.none", {"icon": ICON_BOOKMARK, "label": S.t("menu.empty"), "action": "true"}))
    lines.append(row(f"{MENU_ID}.zz-refresh", {
        "icon": ICON_REFRESH,
        "label": S.t("menu.refresh"),
        "action": f"exec python3 -I {shq(SELF)} sync --force --notify",
    }))
    lines.extend(moved_rows(lang))
    lines.append(END)
    return lines, total


# ---------------------------------------------------------------- menu file

def read_menu():
    try:
        with open(MENU_FILE, encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return None


def splice(text, block):
    """Return the menu text with the managed block replaced/inserted/removed."""
    if text is None:
        return "{\n" + "\n".join(block) + "\n}\n" if block else None
    lines = text.split("\n")
    try:
        b = next(i for i, l in enumerate(lines) if l.rstrip() == BEGIN.rstrip())
        e = next(i for i in range(b, len(lines)) if lines[i].rstrip() == END.rstrip())
        return "\n".join(lines[:b] + block + lines[e + 1:])
    except StopIteration:
        pass
    if not block:
        return text
    # Insert right after the opening brace, so the user's own rows keep their
    # commas as they are and our trailing commas are always followed by more.
    for i, l in enumerate(lines):
        s = l.strip()
        if s.startswith("//") or not s:
            continue
        if s == "{":
            return "\n".join(lines[: i + 1] + block + lines[i + 1:])
        break
    raise RuntimeError(f"cannot find the opening '{{' on its own line in {MENU_FILE}; not touching it")


def write_menu(text):
    os.makedirs(os.path.dirname(MENU_FILE), exist_ok=True)
    # Write in place (not rename) so the shell's file watcher keeps following it;
    # the menu keeps its previous rows if it happens to read a half-written file.
    with open(MENU_FILE, "w", encoding="utf-8") as f:
        f.write(text)


# ---------------------------------------------------------------- commands

def notify(summary, body=""):
    if shutil.which("notify-send"):
        subprocess.run(["notify-send", "-a", "Bookmark Launcher", "-i", "bookmark-new", summary, body],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def cmd_sync(force, notify_done):
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(LOCK_FILE, "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        lang = current_language()
        S = Strings(lang)
        sources = discover()
        sig = signature(sources, lang)
        text = read_menu()
        has_block = text is not None and BEGIN in text
        try:
            with open(SIGNATURE_FILE) as f:
                old = f.read().strip()
        except OSError:
            old = ""
        if not force and has_block and old == sig:
            return 0
        block, total = render(sources, S, lang)
        new = splice(text, block)
        if new != text:
            write_menu(new)
        with open(SIGNATURE_FILE, "w") as f:
            f.write(sig)
        if notify_done:
            browsers = len({s["browser"]["key"] for s in sources})
            notify(S.t("notify.title"), S.tn("notify.body", total, browsers))
    return 0


def cmd_remove():
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(LOCK_FILE, "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        text = read_menu()
        new = splice(text, [])
        if new is not None and new != text:
            write_menu(new)
        try:
            os.remove(SIGNATURE_FILE)
        except OSError:
            pass
    return 0


def cmd_open(key, profile, url):
    browser = BY_KEY.get(key)
    url = clean_url(url)
    if not browser or not url:
        print("bookmarks: refusing to open: unknown browser or non-http(s) URL", file=sys.stderr)
        return 2

    run = browser["run"]
    if browser["kind"] == "chromium":
        args = ([f"--profile-directory={profile}"] if profile else []) + ["--", url]
    else:
        args = (["-P", profile] if profile else []) + ["--new-tab", url]

    if run.startswith("flatpak:"):
        argv = ["flatpak", "run", run.split(":", 1)[1]] + args
    else:
        argv = [run] + args
        # A running Chromium browser takes the URL over its socket instantly.
        if browser["kind"] == "chromium" and shutil.which("omarchy-cmd-browser-handoff"):
            if subprocess.run(["omarchy-cmd-browser-handoff"] + argv,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0:
                argv = None

    if argv:
        launcher = ["uwsm-app", "--"] if shutil.which("uwsm-app") else []
        if shutil.which("systemd-run"):
            launcher = ["systemd-run", "--user", "--quiet", "--collect",
                        f"--unit={re.sub(r'[^A-Za-z0-9-]', '-', PLUGIN_ID)}-{time.time_ns()}",
                        "--property=StandardOutput=null", "--property=StandardError=null"] + launcher
        subprocess.Popen(launcher + argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)

    if os.environ.get("HYPRLAND_INSTANCE_SIGNATURE") and shutil.which("omarchy-hyprland-focus-app"):
        time.sleep(0.3)
        subprocess.run(["omarchy-hyprland-focus-app", browser["wm"]],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return 0


def main(argv):
    if len(argv) >= 1 and argv[0] == "sync":
        return cmd_sync("--force" in argv, "--notify" in argv)
    if len(argv) == 4 and argv[0] == "open":
        return cmd_open(argv[1], argv[2], argv[3])
    if argv == ["remove"]:
        return cmd_remove()
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
