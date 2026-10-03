**Evergreen @VERSION@ for Windows (64-bit)**: a preview of Evergreen, built on Firefox @FIREFOX@ (from Mozilla's source) by this repository's [Windows build workflow](@RUN_URL@).

### Download

| File | What it is |
|---|---|
| `Evergreen-@VERSION@-win64-setup.exe` | Installer. Installs Evergreen like any Windows app. |
| `Evergreen-@VERSION@-win64-portable.zip` | Portable copy. Unzip anywhere and run `evergreen\evergreen.exe`. |
| `SHA256SUMS.txt` | Checksums for both files. |

**Windows SmartScreen will warn you** ("Windows protected your PC"), because Evergreen is not code-signed yet. Choose **More info**, then **Run anyway**. To check a download first, compare its checksum with `SHA256SUMS.txt`:

```
Get-FileHash .\Evergreen-@VERSION@-win64-setup.exe -Algorithm SHA256
```

### What's in this preview

- A **start page** in the Space's colour with a search box under "Evergreen". **New tab** (`Ctrl+T` or the + button) opens a search box over the page, as in Arc: type and press Enter to open a new tab, or Escape to cancel. Both search with your default search engine and suggest pages from your history and bookmarks.
- **New tabs open at the top** of the tab list.
- Arc-style **Spaces** in the vertical-tab sidebar, optionally with **separate sign-ins** (each Space in its own container), **Favorites** (pinned tabs), **Keep in Space**, and an **Archive** for tabs unused for 12 hours. Click a Space's name to rename it.
- **Hide the sidebar** with the sidebar button next to the tree menu (or `Ctrl+Alt+Z`); move the mouse to the left edge of the window to slide it back in over the page. It slides away as soon as the mouse leaves.
- On first run, Evergreen offers to **import** bookmarks, passwords and history from another browser on your computer.
- Hardened defaults: HTTPS-Only, strict tracking protection, encrypted DNS (Quad9), no telemetry or studies compiled in, no sponsored content, AI features blocked, uBlock Origin bundled.
- **Ecosia** is the default search engine; Google, DuckDuckGo and custom engines are in Settings › Search.
- Its own profile (`%APPDATA%\Mozilla\Evergreen`): it does not touch an installed Firefox.

### Known limitations

- **No automatic updates yet.** Install a newer release to update.
- Preview quality: expect rough edges. See the [design document](@REPO_URL@/blob/@SHA@/docs/design.md) for what is planned.

### Checksums

```
@SHA256SUMS@
```
