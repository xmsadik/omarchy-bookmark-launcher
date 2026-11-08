# Bookmark Launcher: notes for agents

Omarchy shell plugin (`xmsadik.bookmark-launcher`, kind `service`). It shows browser bookmarks in the Omarchy launcher (Super+Space) under **Bookmarks**, just above About, searchable there. Each bookmark opens in the browser and profile it came from.

Published at https://github.com/xmsadik/omarchy-bookmark-launcher and listed in the Okomart catalog (`plugins.txt` in https://github.com/brianblakely/omarchy-plugins).

## Where to work

- Edit **this repository** only.
- Never edit the installed copy at `~/.config/omarchy/plugins/xmsadik.bookmark-launcher/`. `omarchy plugin update` fast-forwards that checkout and rejects local changes.
- Never edit anything under `/usr/share/omarchy/` (`$OMARCHY_PATH`). Reading it is fine, and it is the reference for menu and shell behavior.

## Architecture

| File | Role |
| --- | --- |
| `manifest.json` | id, version, kind `service`, entry `Service.qml` |
| `Service.qml` | Runs `bookmarks.py sync` 1.5 s after load, then every 10 s. On unload (disable, remove, reload) it deletes the generated block with self-contained inline Python. |
| `bookmarks.py` | `sync [--force] [--notify]`, `open <key> <profile> <url>`, `remove` |
| `i18n/<lang>.json` | Plugin strings. Fallback order: full code → base language → `en`. Plurals use `one`/`other`. |
| `tests/test_bookmarks.py` | stdlib unittest; no dependencies |

Why it works this way:

- **Launcher providers are hardcoded.** They live in `$OMARCHY_PATH/shell/plugins/menu/Menu.qml`, so a plugin cannot register one.
- **Static rows instead.** The plugin writes rows into the user menu extension `~/.config/omarchy/extensions/omarchy-menu.jsonc`, which the launcher hot-reloads and searches.
- **The block is marker-delimited.** It sits between `  // >>> xmsadik.bookmark-launcher: generated automatically, edits here are overwritten` and `  // <<< xmsadik.bookmark-launcher`, inserted right after the opening `{`. These strings appear in both `bookmarks.py` (BEGIN/END) and `Service.qml` (cleanup). **Change them together.** A test checks that they match.
- **Placement above About.** Menu order is the order in which ids first appear, defaults first, so new root rows always land last.
  - `moved_rows()` hides the default root rows from `about` onward with `"when": "false"`. Hidden rows stay routable and their children stay searchable.
  - It then adds copies right after Bookmarks: an action keeps its action, a submenu becomes a `target` link.
  - Icons and actions come from `$OMARCHY_PATH/default/omarchy/omarchy-menu.jsonc`, and labels from `$OMARCHY_PATH/default/i18n/<lang>/menu.json`, both read at sync time.
- **Change detection.** A signature of the bookmark files' path, mtime and size, plus the language and Omarchy's menu and catalog files, is stored in `~/.cache/xmsadik.bookmark-launcher/signature`. Unchanged runs exit early. `flock` serializes writers.
- **Browser sources.**
  - Chromium-family: `Bookmarks` and `AccountBookmarks` per profile, merged and de-duplicated by URL. Profile names come from `Local State`.
  - Firefox-family: `places.sqlite`, read from a temp copy that includes `-wal`. The `toolbar_____` root is flattened, and `tags________` is skipped.
- **Browser table.** The `BROWSERS` list in `bookmarks.py` is the single source of truth for keys, data roots, binaries (`flatpak:<app-id>` for Flatpak builds) and window classes. A browser is listed only when its binary is installed and it has bookmarks.

## Security rules (do not weaken)

Bookmark titles and URLs are untrusted input written into a file that the shell executes actions from.

- Only `http`/`https` URLs with a host, no control characters, and at most 2048 characters (`clean_url`).
- Every string goes through `js()`, which applies `ensure_ascii` and escapes `}` and `]`. This survives the menu's raw-text `stripJsonc` (`$OMARCHY_PATH/shell/plugins/menu/MenuModel.js`), which strips `,` before `}`/`]` and drops lines that start with `//`. Lists go through `js_value()`.
- Values reach the action string only through `shq()` single-quoting. `open` validates the URL again and launches with an argv list, never through a shell.
- Labels are sanitized and capped at 160 characters. The menu renders them with `Text.PlainText`.
- No network access, keybindings, install hooks, symlinks or privileged setup. These are Okomart rules too.
- Add a regression test for any new place that untrusted text reaches.

## Workflow

```bash
python3 -m unittest discover tests      # must pass
omarchy plugin validate .               # must pass
python3 -I bookmarks.py sync --force    # regenerate now (uses the real menu file)
# Safe dry run against a copy:
BOOKMARKS_MENU_FILE=/tmp/x.jsonc BOOKMARKS_CACHE_DIR=/tmp/xc python3 -I bookmarks.py sync --force
```

Release:

1. Bump `version` in `manifest.json`: patch for fixes, minor for features.
2. Commit and `git push`.
3. Run `omarchy plugin update xmsadik.bookmark-launcher` to try it locally. Users update the same way.
4. Okomart needs no change. It follows the repository's HEAD.

Check runtime errors with `journalctl --user --since -5min | grep -i bookmark`. Check the result with `omarchy menu summon bookmarks`.

Keep `README.md` in the Okomart format: Install, Update, Validate from source, and Security sections. Document any new command, file access or background behavior there.
