# Prefs audit

What this is: Evergreen's default prefs (`prefs/evergreen.js`) compared with
[arkenfox's `user.js`](https://github.com/arkenfox/user.js), the best-known
hardening list for Firefox, and with Firefox 160's own defaults. Done 2026-11-24
(M0). Rerun it on every Firefox bump and now and then against arkenfox's newest:

```
python eg.py audit-prefs                 # fetches arkenfox's user.js and Firefox's pref files
python eg.py audit-prefs --file user.js  # a local copy of user.js
```

The tool (`tools/eg/audit.py`, tested in `tests/python/test_audit.py`) only
reports; every decision below is a person's. The list of what is *not*
adopted matters as much as what is.

## Result

arkenfox sets 151 prefs (without its own markers). Before the audit Evergreen
set the same value for 36 and a different one for 1 (`browser.startup.page`:
intended, Spaces live in the session). Of the 114 it did not set, Firefox's
default already matches arkenfox for 31. The audit added **28 prefs** (the
section "Added by the prefs audit" in `prefs/evergreen.js`; 12 more were found
not to exist in Firefox 160 and were dropped by `eg.py check-prefs`).

## Adopted

| Pref | Why |
|---|---|
| `browser.places.speculativeConnect.enabled` = false | no connection to a page the user did not ask for |
| `network.proxy.socks_remote_dns` = true | DNS goes through a SOCKS proxy, not around it |
| `dom.security.https_only_mode_send_http_background_request` = false | HTTPS-Only otherwise sends a plain HTTP request in the background |
| `network.auth.subresource-http-auth-allow` = 1 | cross-site sub-resources cannot pop up a login box (phishing) |
| `media.peerconnection.ice.proxy_only_if_behind_proxy` = true | WebRTC does not bypass a proxy |
| `network.IDN_show_punycode` = true | look-alike domains are shown as `xn--` (the price: real internationalised domains look ugly) |
| `security.ssl.treat_unsafe_negotiation_as_broken`, `browser.xul.error_pages.expert_bad_cert` | clearer TLS problems |
| `dom.disable_window_move_resize` = true | a page cannot move or resize the window (Evergreen centres popups itself) |
| `browser.uitour.enabled` = false | Mozilla's pages cannot drive the browser's UI |
| `security.csp.reporting.enabled` = false | no violation reports sent to sites' report endpoints |
| `signon.formlessCapture.enabled` = false | no password capture from forms that are not forms |
| `browser.helperApps.deleteTempFileOnExit` = true | files opened from a download do not stay in the temp folder |
| `browser.urlbar.suggest.searches` and five more `*.featureGate` | no search or "market" suggestions in the address bar |
| `toolkit.telemetry.server` = `data:,` and five ping prefs | a further layer where telemetry code is not compiled in anyway |
| `app.normandy.api_url`, `breakpad.reportURL` = "" | no studies endpoint, no crash-report endpoint |
| the two `asrouter.userprefs.cfr.*` prefs | no add-on or feature recommendations |

## Not adopted, and why

| Pref (arkenfox / Firefox) | Reason |
|---|---|
| `browser.startup.page` 0 / Evergreen 3 | Spaces and tabs are restored by design |
| `privacy.resistFingerprinting` and its relatives, `privacy.spoof_english`, `privacy.window.maxInnerWidth` | strict fingerprinting resistance breaks sites; an opt-in (decision Q11) |
| `privacy.sanitize.sanitizeOnShutdown`, `privacy.clearOnShutdown*`, `privacy.clearHistory*` | Evergreen keeps history and sign-ins; clearing is the user's choice |
| `browser.cache.disk.enable` = false | a large speed cost for little gain on a personal machine |
| `browser.sessionstore.privacy_level` = 2 | would drop form data and cookies from session restore |
| `network.captive-portal-service.enabled`, `network.connectivity-service.enabled`, `captivedetect.canonicalURL` | hotspot logins stop being detected; the checks are small requests to Mozilla's own host (decision Q6) |
| `geo.provider.use_geoclue`, `geo.provider.ms-windows-location`, `geo.provider.use_corelocation` | location needs a provider; asking permission per site is the control |
| `browser.formfill.enable` = false | form history is local; a convenience, not a leak |
| `security.ssl.require_safe_negotiation` = true | breaks old servers; the browser already marks them unsafe |
| `security.cert_pinning.enforcement_level` = 2 | breaks TLS-inspecting antivirus; Firefox's level 1 still pins Mozilla's own hosts |
| `browser.download.start_downloads_in_tmp_dir` = true | the download manager writes its part files next to the target |
| `browser.download.useDownloadDir` = false, `...always_ask_before_handling_new_types` = true | a prompt for every download; the Downloads panel is the Arc way |
| `extensions.postDownloadThirdPartyPrompt` = false | the prompt is a protection |
| `browser.aboutConfig.showWarning`, `browser.startup.homepage`, `browser.newtabpage.enabled` | Evergreen draws its own start page; the warning is a convenience |
| `permissions.manager.defaultsUrl` = "" | removes Firefox's built-in default permissions |
| `toolkit.winRegisterApplicationRestart`, `browser.shell.shortcutFavicons` | Windows integration; harmless |

## Network egress

`tests/smoke/smoke_test.py --egress` routes the browser's traffic through a
recording proxy that answers nothing, and prints every host the browser tried to
reach during the smoke run (see development.md, "Egress test"). Hosts outside
`tests/smoke/egress-allowed.txt` fail the run once that file lists what Evergreen
is allowed to contact. The first list comes from a real run's output.
