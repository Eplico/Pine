**Evergreen @VERSION@ for Linux (64-bit)**, built on Firefox @FIREFOX@ (from Mozilla's source) by this repository's [Linux build workflow](@RUN_URL@). Evergreen is an early preview.

### Download

| File | What it is |
|---|---|
| `Evergreen-@VERSION@-linux-x86_64-portable.tar.*` | Portable copy. Unpack it anywhere and run `evergreen/evergreen`. |
| `SHA256SUMS.txt` | Checksum for the file above. |

```
tar -xf Evergreen-@VERSION@-linux-x86_64-portable.tar.*
./evergreen/evergreen
```

It needs the libraries any Firefox needs (GTK 3, D-Bus, ALSA or PulseAudio). To check the download first, compare `sha256sum` of it with `SHA256SUMS.txt`.

Evergreen keeps its own profile, separate from Firefox's, so it never touches an installed Firefox.

### What's in Evergreen @VERSION@

See the [Windows release notes](@REPO_URL@/releases) for the feature list: it is the same browser. In short: Arc-style Spaces, big Favorites tiles, a compact address bar with room to drag the window, Arc-style popup windows, an Archive for idle tabs, a start page and search box, hardened defaults, uBlock Origin and Ecosia as the default search engine.

### Known limitations

- **No automatic updates yet.** Unpack a newer release to update.
- No installer or desktop launcher: run `evergreen/evergreen`, or add a launcher of your own pointing at it.
- Preview quality: expect rough edges. See the [design document](@REPO_URL@/blob/@SHA@/docs/design.md) for what is planned.

### Checksums

