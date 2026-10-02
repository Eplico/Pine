/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

/**
 * Dev harness glue. In a real build, Evergreen.manifest registers these entry
 * points with Firefox's category manager; here we wire up the same calls with
 * observers, and register Evergreen's Fluent files from the repo.
 */

const lazy = {};
ChromeUtils.defineESModuleGetters(lazy, {
  EvergreenStartup: "resource://evergreen/EvergreenStartup.sys.mjs",
  EvergreenWindow: "resource://evergreen/EvergreenWindow.sys.mjs",
});

export const EvergreenDev = {
  _started: false,
  _firstWindowSeen: false,

  start() {
    if (this._started) {
      return;
    }
    this._started = true;
    L10nRegistry.getInstance().registerSources([
      new L10nFileSource(
        "evergreen-dev",
        "app",
        ["en-US"],
        "resource://evergreen-l10n/{locale}/"
      ),
    ]);
    Services.obs.addObserver(this, "browser-delayed-startup-finished");
    Services.obs.addObserver(this, "quit-application-granted");
  },

  observe(subject, topic) {
    if (topic == "browser-delayed-startup-finished") {
      let win = subject;
      if (!this._firstWindowSeen) {
        this._firstWindowSeen = true;
        lazy.EvergreenStartup.firstWindowReady();
      }
      lazy.EvergreenWindow.init(win);
      win.addEventListener("unload", () => lazy.EvergreenWindow.uninit(win), {
        once: true,
      });
    } else if (topic == "quit-application-granted") {
      lazy.EvergreenStartup.quit();
    }
  },
};
