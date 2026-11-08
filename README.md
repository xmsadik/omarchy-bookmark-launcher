# Bookmark Launcher

Search your browser bookmarks right inside the Omarchy launcher. Bookmarks from
Chrome, Firefox and friends show up under **Bookmarks** in the menu, and typing a
title or a domain in the launcher finds them. Each bookmark opens in the browser
it came from (and, for Chromium browsers, in the right profile).

## Features

- Lives in the Omarchy menu: no separate overlay or extra keybinding to learn.
- Searchable by title, URL, and folder path.
- Each bookmark opens in its own browser and profile, and the window is focused.
- Stays in sync: new or edited bookmarks appear within about 10 seconds, and a
  newly installed browser appears as soon as it has bookmarks.
- **Refresh bookmarks** row for an immediate update.
- Follows Omarchy's language setting (English and Turkish included).

### Supported browsers

| Chromium family | Firefox family |
| --- | --- |
| Chrome (stable, beta, dev), Chromium, Brave (incl. beta, nightly, Origin), Edge (stable, beta, dev), Vivaldi, Helium, Thorium | Firefox, Zen, LibreWolf, Floorp, Waterfox |

Flatpak builds of Chrome, Chromium, Brave, Edge, Firefox, Zen and LibreWolf are
detected too. A browser is shown only when it is installed and has bookmarks.

## Install

```bash
omarchy plugin add https://github.com/xmsadik/omarchy-bookmark-launcher.git --enable
```

Then open the launcher (<kbd>Super</kbd> + <kbd>Space</kbd>) and type the name of
a bookmark, or open **Bookmarks** from the menu.

Requires `python3` (part of the Omarchy base install). No other dependencies.

### Keybinding (optional)

Jump straight to the Bookmarks submenu by adding this to `~/.config/hypr/bindings.lua`:

```lua
o.bind("SUPER + B", "Bookmarks", "omarchy menu summon bookmarks")
```

## How it works

Omarchy's launcher reads extra rows from
`~/.config/omarchy/extensions/omarchy-menu.jsonc` and reloads that file on save.
This plugin is a small background service that reads your browsers' bookmark
files and keeps a clearly marked block of rows in that file up to date:

```jsonc
{
  // >>> xmsadik.bookmark-launcher: generated automatically, edits here are overwritten
  "bookmarks": { ... },
  ...
  // <<< xmsadik.bookmark-launcher
  // ...your own entries stay untouched...
}
```

- Chromium browsers: `Bookmarks` and `AccountBookmarks` in each profile.
- Firefox browsers: `places.sqlite`, read from a temporary copy because the
  browser keeps the live database locked.
- Every 10 seconds the service compares the files' timestamps and sizes, and
  only rewrites the menu when something changed.

## Privacy and safety

- Everything stays on your machine. The plugin makes no network requests.
- Only `http` and `https` bookmarks are imported. `javascript:`, `file:`,
  `data:` and similar links are skipped.
- Bookmark titles and URLs are treated as untrusted: they are escaped before
  they are written to the menu file, the launcher shows them as plain text, and
  a URL reaches the browser only as a single command-line argument after it is
  validated again.

## Uninstall

```bash
omarchy plugin remove xmsadik.bookmark-launcher
```

The generated block is removed from the menu file automatically when the plugin
is disabled or removed. To remove it by hand, delete the lines between the two
`xmsadik.bookmark-launcher` marker comments.

## Development

```bash
python3 -m unittest discover tests        # run the tests
python3 -I bookmarks.py sync --force      # regenerate now
python3 -I bookmarks.py remove            # drop the generated rows
omarchy plugin validate .
```

Translations live in `i18n/<language-code>.json`. Missing strings fall back to
English, so new languages can be added one file at a time.

## License

MIT
