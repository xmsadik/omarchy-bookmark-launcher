import QtQuick
import Quickshell
import Quickshell.Io

// Keeps a generated "Bookmarks" block in the Omarchy launcher's user menu
// (~/.config/omarchy/extensions/omarchy-menu.jsonc) in step with the browsers'
// own bookmark files. All the work happens in bookmarks.py; this service only
// schedules it, and removes the block again when the plugin is unloaded.
Item {
  id: root

  // Injected by omarchy-shell (the service loader).
  property var shell: null

  readonly property string pluginId: "xmsadik.bookmark-launcher"
  readonly property string script: decodeURIComponent(Qt.resolvedUrl("bookmarks.py").toString().replace(/^file:\/\//, ""))
  readonly property string configHome: Quickshell.env("XDG_CONFIG_HOME") || (Quickshell.env("HOME") + "/.config")
  readonly property string cacheHome: Quickshell.env("XDG_CACHE_HOME") || (Quickshell.env("HOME") + "/.cache")
  readonly property string menuFile: configHome + "/omarchy/extensions/omarchy-menu.jsonc"
  readonly property string cacheDir: cacheHome + "/" + pluginId

  function sync() {
    if (!syncProc.running) syncProc.running = true
  }

  // A short delay on load lets a previous instance's cleanup (hot reload,
  // shell restart) finish before the first sync rebuilds the block.
  Timer {
    interval: 1500
    running: true
    repeat: false
    onTriggered: root.sync()
  }

  // Bookmark changes are detected by a cheap mtime/size signature, so most
  // ticks exit without touching the menu file.
  Timer {
    interval: 10000
    running: true
    repeat: true
    onTriggered: root.sync()
  }

  Process {
    id: syncProc
    command: ["nice", "-n", "10", "python3", "-I", root.script, "sync"]
    stderr: StdioCollector {
      waitForEnd: true
      onStreamFinished: if (text.trim()) console.warn("[" + root.pluginId + "] " + text.trim())
    }
  }

  // Omarchy has no uninstall hook, so take the generated rows out of the
  // launcher whenever this service goes away (disable, remove, reload). This is
  // self-contained because `omarchy plugin remove` may already have deleted
  // bookmarks.py; the markers match BEGIN/END in bookmarks.py exactly.
  Component.onDestruction: {
    Quickshell.execDetached([
      "python3", "-I", "-c",
      "import fcntl,os,sys\n" +
      "f,pid,cache=sys.argv[1:4]\n" +
      "b='  // >>> '+pid+': generated automatically, edits here are overwritten'\n" +
      "e='  // <<< '+pid\n" +
      "os.makedirs(cache,exist_ok=True)\n" +
      "l=open(os.path.join(cache,'lock'),'w');fcntl.flock(l,fcntl.LOCK_EX)\n" +
      "try:\n t=open(f,encoding='utf-8').read()\nexcept OSError:\n sys.exit(0)\n" +
      "L=t.split('\\n');r=[x.rstrip() for x in L]\n" +
      "if b in r and e in r[r.index(b):]:\n" +
      " i=r.index(b);j=r.index(e,i)\n" +
      " open(f,'w',encoding='utf-8').write('\\n'.join(L[:i]+L[j+1:]))\n" +
      "try:\n os.remove(os.path.join(cache,'signature'))\nexcept OSError:\n pass\n",
      root.menuFile, root.pluginId, root.cacheDir
    ])
  }
}
