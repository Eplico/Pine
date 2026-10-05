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

// --- Evergreen -------------------------------------------------------------------
// Unpinned tabs not used for this many hours move to the Archive (0 = never).
pref("evergreen.archive.afterHours", 12);
pref("evergreen.archive.maxEntries", 500);
pref("evergreen.archive.maxAgeDays", 30);
