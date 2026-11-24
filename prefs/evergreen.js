/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

// Evergreen default preferences (design doc §8.2).
//
// `eg.py prepare` appends this file to Evergreen's branding prefs file
// (defaults/preferences/firefox-branding.js), which Firefox loads after its
// own firefox.js, so these values win. Users can still change any of them.
//
// Format rules (enforced by `eg.py lint`): one `pref("name", value);` per
// line, comments allowed, no preprocessor. `eg.py check-prefs` fails if a
// name does not exist in Firefox, which catches typos and removed prefs.

// --- Layout: Arc-style vertical tabs in the sidebar --------------------------
pref("sidebar.revamp", true);
pref("sidebar.verticalTabs", true);
pref("sidebar.visibility", "always-show");
// Downloads sits at the start of the toolbar, so it is always shown.
pref("browser.download.autohideButton", false);
pref("browser.tabs.groups.enabled", true);
// Closing the last tab shows the start page instead of closing the window.
pref("browser.tabs.closeWindowWithLastTab", false);
// Containers back Spaces that use separate sign-ins.
pref("privacy.userContext.enabled", true);
pref("privacy.userContext.ui.enabled", true);
// Spaces live in the session: always restore it.
pref("browser.startup.page", 3);

// --- Transport -----------------------------------------------------------------
pref("dom.security.https_only_mode", true);
pref("security.tls.enable_0rtt_data", false);
// DNS over HTTPS through Quad9 (non-profit, no IP logging), falling back to
// system DNS if it fails. Mode 3 (no fallback) is available in Settings.
pref("network.trr.mode", 2);
pref("network.trr.uri", "https://dns.quad9.net/dns-query");
pref("network.trr.custom_uri", "https://dns.quad9.net/dns-query");

// --- Tracking ------------------------------------------------------------------
// ETP Strict is switched on at first run by EvergreenStartup: Firefox only
// applies a content-blocking category that is set as a user value.
pref("privacy.globalprivacycontrol.enabled", true);
pref("network.http.referer.XOriginTrimmingPolicy", 2);
pref("media.peerconnection.ice.default_address_only", true);

// --- No connections the user did not ask for -----------------------------------
pref("network.prefetch-next", false);
pref("network.dns.disablePrefetch", true);
pref("network.dns.disablePrefetchFromHTTPS", true);
pref("network.http.speculative-parallel-limit", 0);
pref("browser.urlbar.speculativeConnect.enabled", false);

// --- Attack surface ------------------------------------------------------------
pref("pdfjs.enableScripting", false);
// Keep Safe Browsing's local lists, but do not send download metadata away.
pref("browser.safebrowsing.downloads.remote.enabled", false);
// Fill saved logins only when the user picks one.
pref("signon.autofillForms", false);
// Strict fingerprinting resistance breaks many sites; it is an opt-in.
pref("privacy.resistFingerprinting", false);

// --- Search (Ecosia is made the default at first run by EvergreenStartup) -------
// Suggestions send every keystroke to the search engine: off until opted in.
pref("browser.search.suggest.enabled", false);
pref("browser.urlbar.trending.featureGate", false);
pref("browser.urlbar.quicksuggest.enabled", false);
pref("browser.urlbar.suggest.quicksuggest.sponsored", false);
pref("browser.urlbar.suggest.quicksuggest.nonsponsored", false);
pref("browser.urlbar.addons.featureGate", false);
pref("browser.urlbar.mdn.featureGate", false);
pref("browser.urlbar.weather.featureGate", false);
pref("browser.urlbar.yelp.featureGate", false);

// --- Telemetry, studies, experiments (not compiled in; this is a second layer) --
pref("datareporting.healthreport.uploadEnabled", false);
pref("datareporting.policy.dataSubmissionEnabled", false);
pref("datareporting.usage.uploadEnabled", false);
pref("toolkit.telemetry.unified", false);
pref("toolkit.telemetry.archive.enabled", false);
pref("app.shield.optoutstudies.enabled", false);
pref("app.normandy.enabled", false);
pref("nimbus.rollouts.enabled", false);
pref("browser.discovery.enabled", false);
pref("browser.newtabpage.activity-stream.telemetry", false);
pref("browser.tabs.crashReporting.sendReport", false);
pref("browser.crashReports.unsubmittedCheck.autoSubmit2", false);

// --- Sponsored content and recommendations -----------------------------------
pref("browser.newtabpage.activity-stream.showSponsored", false);
pref("browser.newtabpage.activity-stream.showSponsoredTopSites", false);
pref("browser.newtabpage.activity-stream.feeds.section.topstories", false);
pref("browser.newtabpage.activity-stream.showWeather", false);
pref("extensions.htmlaboutaddons.recommendations.enabled", false);

// --- AI features: blocked by default (Firefox 148+ AI controls) ----------------
pref("browser.ai.control.default", "blocked");
pref("browser.ml.chat.enabled", false);
pref("browser.ml.linkPreview.enabled", false);
pref("browser.tabs.groups.smart.enabled", false);
pref("browser.smartwindow.enabled", false);
pref("extensions.ml.enabled", false);

// --- Firefox onboarding (Evergreen will ship its own) ---------------------------
pref("browser.aboutwelcome.enabled", false);
// Mozilla's first-run notices are about Mozilla's Firefox: its Terms of Use
// modal and the data-collection privacy notice (which opens a tab). Evergreen
// is a different product that collects no data. Non-official builds such as
// Evergreen's already skip the Terms of Use modal; these make the dev harness
// on official Firefox behave the same.
pref("termsofuse.bypassNotification", true);
pref("browser.preonboarding.enabled", false); // eg:dynamic Nimbus fallbackPref of the preonboarding feature
pref("datareporting.policy.dataSubmissionPolicyBypassNotification", true);

// --- Added by the prefs audit against arkenfox (docs/prefs-audit.md) -----------
// Network: nothing guessed, nothing leaked around the proxy or HTTPS-Only.
pref("browser.places.speculativeConnect.enabled", false);
pref("network.proxy.socks_remote_dns", true);
pref("dom.security.https_only_mode_send_http_background_request", false);
pref("network.auth.subresource-http-auth-allow", 1);
pref("media.peerconnection.ice.proxy_only_if_behind_proxy", true);
// Show internationalised domain names as punycode (look-alike domains).
pref("network.IDN_show_punycode", true);
pref("security.ssl.treat_unsafe_negotiation_as_broken", true);
pref("browser.xul.error_pages.expert_bad_cert", true);
// Pages cannot move or resize the window; Mozilla's UI tour pages cannot drive the UI.
pref("dom.disable_window_move_resize", true);
pref("browser.uitour.enabled", false);
pref("security.csp.reporting.enabled", false);
pref("signon.formlessCapture.enabled", false);
pref("browser.helperApps.deleteTempFileOnExit", true);
// No search suggestions, even if the engine offers them.
pref("browser.urlbar.suggest.searches", false);
pref("browser.urlbar.amp.featureGate", false);
pref("browser.urlbar.importantDates.featureGate", false);
pref("browser.urlbar.market.featureGate", false);
pref("browser.urlbar.wikipedia.featureGate", false);
pref("browser.urlbar.yelpRealtime.featureGate", false);
// Telemetry: more layers (the code is not compiled in either).
pref("toolkit.telemetry.server", "data:,");
pref("toolkit.telemetry.bhrPing.enabled", false);
pref("toolkit.telemetry.firstShutdownPing.enabled", false);
pref("toolkit.telemetry.newProfilePing.enabled", false);
pref("toolkit.telemetry.shutdownPingSender.enabled", false);
pref("app.normandy.api_url", "");
pref("breakpad.reportURL", "");
// No add-on or feature recommendations.
pref("browser.newtabpage.activity-stream.asrouter.userprefs.cfr.addons", false);
pref("browser.newtabpage.activity-stream.asrouter.userprefs.cfr.features", false);

// No lookup of the country from the network address (location.services.mozilla.com):
// the search engine is chosen by Evergreen, not by region.
pref("browser.region.update.enabled", false);
pref("browser.region.network.url", "");

// --- Evergreen -------------------------------------------------------------------
// Unpinned tabs not used for this many hours move to the Archive (0 = never).
pref("evergreen.archive.afterHours", 12);
pref("evergreen.archive.maxEntries", 500);
pref("evergreen.archive.maxAgeDays", 30);

// --- Updates and moving to a new installation ---------------------------------
// (The build adds the version number to compare against; see eg.py prepare.)
// Look for a newer Evergreen once a day: one request to GitHub's public release
// list, with no cookies or identifiers. Turn off to update only by hand.
pref("evergreen.update.check", true);
pref("evergreen.update.url", "https://api.github.com/repos/Eplico/Pine/releases?per_page=30");
// Where a release's files are downloaded from (https only; the files are checked against the release's SHA256SUMS.txt).
pref("evergreen.update.downloadBase", "https://github.com/Eplico/Pine/releases/download/");
// On the first run of a new installation folder, offer the earlier profile.
pref("evergreen.firstrun.profilePrompt", true);

// --- Accelerated downloads -------------------------------------------------------
// Large downloads use several connections at once and can resume (see
// docs/download-manager.md). Off: Firefox's own downloader, as before.
pref("evergreen.downloads.enabled", true);
pref("evergreen.downloads.connections", 6);   // per download, 1 to 16
pref("evergreen.downloads.minSizeMB", 8);     // smaller files are left to Firefox
pref("evergreen.downloads.maxSizeGB", 64);    // larger files are left to Firefox
pref("evergreen.downloads.excludeHosts", ""); // comma-separated; subdomains included
pref("evergreen.downloads.maxActive", 3);      // accelerated downloads at once; the rest wait (0 = no limit)
pref("evergreen.downloads.maxConnections", 32); // connections open across all downloads (0 = no limit)
pref("evergreen.downloads.log", false);       // decisions and retries to the browser console

// Link routing (design §7.8): a JSON list of { host, spaceId }; set from the
// command bar ("Always open <site> in <Space>"), not by hand.
pref("evergreen.routing.rules", "[]");
// Peek (design §7.6): a link from a Favorite to another site opens in a mini window.
pref("evergreen.peek.enabled", true);
// Open links from other apps in a mini window (decision Q8: off by default).
pref("evergreen.externalLinks.miniWindow", false);
