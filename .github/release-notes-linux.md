**Evergreen @VERSION@ for Linux (64-bit)**, built on Firefox @FIREFOX@ (from Mozilla's source) by this repository's [Linux build workflow](@RUN_URL@). Evergreen is an early preview.

### Install (and update)

Download **`evergreen-install`** below, then:

```
python3 evergreen-install
```

That downloads this release, checks it against `SHA256SUMS.txt`, installs it under `~/.local/share/evergreen` and adds **Evergreen to your app menu** with its icon. Pin it from there as you would any app. To update later, Evergreen shows an "update available" bar: click *Update*, then *Restart*. (Or run `~/.local/share/evergreen/evergreen-install update`.)

It uses one fixed folder on purpose: Firefox ties a profile to the folder it runs from, so updating in place keeps your tabs, Spaces, logins and settings. Your data lives in your profile (not in the install folder), so uninstalling (`evergreen-install uninstall`) never touches it.

| File | What it is |
|---|---|
| `evergreen-install` | The installer and updater (Python 3, nothing else needed). |
| `Evergreen-@VERSION@-linux-x86_64-portable.tar.*` | The program itself, for the installer above, or unpack it anywhere and run `evergreen/evergreen`. |
| `SHA256SUMS.txt` | Checksums of both files. |

**Moving from an older copy** (one you unpacked yourself)? Install with the steps above and start Evergreen: on its first start it offers to **use your earlier data** (it picks the profile with the most data, and nothing is deleted or copied).

It needs the libraries any Firefox needs (GTK 3, D-Bus, ALSA or PulseAudio).

Evergreen keeps its own profile, separate from Firefox's, so it never touches an installed Firefox.

### What's in Evergreen @VERSION@

See the [Windows release notes](@REPO_URL@/releases) for the feature list: it is the same browser. In short: Arc-style Spaces, big Favorites tiles, a compact address bar with room to drag the window, Arc-style popup windows, an Archive for idle tabs, a start page and search box, hardened defaults, uBlock Origin and Ecosia as the default search engine. New in this release: the **command bar** (`Ctrl+T`, then type, or `>` for commands), **link routing** (a site always opens in a Space you choose), **Peek** (links from a Favorite to another site open in a small mini window), and the **download queue**; before that, a built-in **download manager** (large files download on several connections at once, retry from where they stopped and resume after a restart; set `evergreen.downloads.enabled` to `false` in `about:config` to use Firefox's own downloader), and, on Linux, a proper app-menu entry and icon, one-click updates and carrying your data to a new installation.

### Known limitations

- Updates are downloaded over HTTPS from this repository's releases and checked against the checksums published with them; they are not yet signed with a separate key.
- To turn off the daily "is there a newer release?" request to GitHub, set `evergreen.update.check` to `false` in `about:config`.
- Preview quality: expect rough edges. See the [design document](@REPO_URL@/blob/@SHA@/docs/design.md) for what is planned.

### Checksums
