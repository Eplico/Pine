/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

/**
 * Evergreen's search box, drawn over the page area in two ways:
 *
 *  - Start page: a tab showing Firefox's home or new-tab page gets a solid
 *    page, tinted with the Space's colour, with "Evergreen" above a centered
 *    search box. Enter loads in that tab.
 *  - New tab (Ctrl+T, the New Tab button): the box floats over the current
 *    page, as Arc's command bar does. Enter opens a new tab (at the top of
 *    the list, in the Space's container: EvergreenWindow); Escape or a click
 *    outside closes it without opening one.
 *
 * Text that is an address opens it; anything else searches with the default
 * search engine (Firefox's own address parsing decides, as in the address
 * bar). Below the box, pages from history and bookmarks that match.
 *
 * Security (design doc §6.2): page titles and URLs from history are only ever
 * written as text, and only http(s), file and about: addresses are opened.
 */

import { getSearchService } from "./SearchDefaults.sys.mjs";

const HTML_NS = "http://www.w3.org/1999/xhtml";
const START_PAGES = new Set(["about:home", "about:newtab"]);
const OPENABLE_SCHEMES = new Set(["http", "https", "file", "about", "view-source"]);
const MAX_HISTORY_RESULTS = 5;
const SUGGEST_DELAY_MS = 50;
// Firefox focuses the address bar when a new-tab page loads; the box takes
// focus back if that happens this soon after it opens.
const REFOCUS_WINDOW_MS = 400;

function html(doc, tag, attrs = {}) {
  let node = doc.createElementNS(HTML_NS, tag);
  for (let [k, v] of Object.entries(attrs)) {
    node.setAttribute(k, v);
  }
  return node;
}

/**
 * What typed text opens: { url, search } where search is true for a search
 * with the default engine. Null when it opens nothing.
 */
export function resolveInput(text, { isPrivate = false, uriFixup = Services.uriFixup } = {}) {
  text = text.trim();
  if (!text) {
    return null;
  }
  let flags = Ci.nsIURIFixup.FIXUP_FLAG_ALLOW_KEYWORD_LOOKUP | Ci.nsIURIFixup.FIXUP_FLAG_FIX_SCHEME_TYPOS;
  if (isPrivate) {
    flags |= Ci.nsIURIFixup.FIXUP_FLAG_PRIVATE_CONTEXT; // the private-browsing engine
  }
  let info;
  try {
    info = uriFixup.getFixupURIInfo(text, flags);
  } catch {
    return null;
  }
  let uri = info.preferredURI;
  if (!uri || !OPENABLE_SCHEMES.has(uri.scheme)) {
    return null;
  }
  return { url: uri.spec, search: !!info.keywordProviderName };
}

export function isStartPage(spec) {
  return START_PAGES.has(spec);
}

/** `text` for a LIKE pattern with "/" as the escape character. */
export function escapeLike(text) {
  return text.replace(/[/%_]/g, "/$&");
}

export class SearchBar {
  constructor(win) {
    this.win = win;
    this.doc = win.document;
    this.mode = null; // "start" | "launcher" | null
    this.results = []; // [{ url, kind: "search" | "visit" | "page", text?, title? }]
    this.selected = 0;
    this.engineName = "";
    this.isPrivate = win.PrivateBrowsingUtils?.isWindowPrivate(win) ?? false;
    this._cleanups = [];
    this._suggestTimer = null;
    this._query = 0;
  }

  start() {
    let { win, doc } = this;
    let tabbox = doc.getElementById("tabbrowser-tabbox");
    if (!tabbox) {
      return;
    }
    let root = html(doc, "div", { id: "evergreen-search", hidden: "true" });
    let box = html(doc, "div", { class: "evergreen-search-box", role: "search" });
    let title = html(doc, "h1", { class: "evergreen-search-title", "data-l10n-id": "evergreen-search-title" });
    let input = html(doc, "input", {
      id: "evergreen-search-input",
      type: "text",
      autocomplete: "off",
      spellcheck: "false",
      role: "combobox",
      "aria-autocomplete": "list",
      "aria-controls": "evergreen-search-results",
      "aria-expanded": "false",
    });
    let list = html(doc, "ul", { id: "evergreen-search-results", role: "listbox" });
    box.append(title, input, list);
    root.append(box);
    tabbox.append(root);
    this.root = root;
    this.input = input;
    this.list = list;
    this._cleanups.push(() => root.remove());

    input.addEventListener("input", () => this.scheduleSuggestions());
    input.addEventListener("keydown", e => this.onKeyDown(e));
    // A click outside the box closes the floating box.
    root.addEventListener("mousedown", e => {
      if (e.target == root) {
        this.closeLauncher();
      }
    });
    list.addEventListener("click", e => {
      let row = e.target.closest("li");
      if (row) {
        this.selected = Number(row.dataset.index);
        this.openSelected();
      }
    });

    // The new-tab command opens the floating box instead of a blank tab.
    let commands = win.BrowserCommands;
    if (commands?.openTab) {
      let original = commands.openTab;
      commands.openTab = (options = {}) => {
        if (options.url) {
          return original.call(commands, options);
        }
        return this.openLauncher();
      };
      this._cleanups.push(() => (commands.openTab = original));
    }

    // The start page follows the selected tab and its address. Switching tabs
    // closes the floating box.
    let update = () => this.updateStartPage();
    let onTabSelect = () => {
      this.closeLauncher({ restoreFocus: false });
      update();
    };
    let tabs = win.gBrowser.tabContainer;
    tabs.addEventListener("TabSelect", onTabSelect);
    this._cleanups.push(() => tabs.removeEventListener("TabSelect", onTabSelect));
    let progress = {
      onLocationChange: (webProgress, request, location) => {
        if (webProgress.isTopLevel) {
          update();
        }
      },
      QueryInterface: ChromeUtils.generateQI(["nsIWebProgressListener", "nsISupportsWeakReference"]),
    };
    win.gBrowser.addProgressListener(progress);
    this._cleanups.push(() => win.gBrowser.removeProgressListener(progress));
    update();
  }

  stop() {
    this.win.clearTimeout(this._suggestTimer);
    for (let fn of this._cleanups.reverse()) {
      try {
        fn();
      } catch (e) {
        console.error(e);
      }
    }
    this._cleanups = [];
  }

  get selectedIsStartPage() {
    let browser = this.win.gBrowser.selectedBrowser;
    return isStartPage(browser?.currentURI?.spec ?? "");
  }

  // --- showing and hiding -------------------------------------------------------

  async show(mode) {
    this.mode = mode;
    this.root.setAttribute("mode", mode);
    // Over a start page the box keeps the solid page, even for a new tab.
    this.root.toggleAttribute("solid", this.selectedIsStartPage);
    this.root.hidden = false;
    this.input.value = "";
    this.renderResults([]);
    this.focusInput();
    let engine = null;
    try {
      let search = getSearchService().service;
      engine = await (this.isPrivate ? search.getDefaultPrivate() : search.getDefault());
    } catch (e) {
      console.error(e);
    }
    this.engineName = engine?.name ?? "";
    if (this.engineName) {
      this.doc.l10n.setAttributes(this.input, "evergreen-search-input", { engine: this.engineName });
    } else {
      this.doc.l10n.setAttributes(this.input, "evergreen-search-input-no-engine");
    }
  }

  focusInput() {
    let mode = this.mode;
    let { win, input } = this;
    let until = win.performance.now() + REFOCUS_WINDOW_MS;
    let urlbar = win.gURLBar?.inputField;
    let focus = () => {
      if (this.mode == mode) {
        input.focus();
      }
    };
    let onUrlbarFocus = () => {
      if (win.performance.now() < until) {
        focus();
      }
    };
    urlbar?.addEventListener("focus", onUrlbarFocus);
    win.setTimeout(() => urlbar?.removeEventListener("focus", onUrlbarFocus), REFOCUS_WINDOW_MS);
    win.requestAnimationFrame(focus);
  }

  hide() {
    this.mode = null;
    this.root.hidden = true;
    this.root.removeAttribute("mode");
    this.root.removeAttribute("solid");
    this.renderResults([]);
  }

  updateStartPage() {
    if (this.mode == "launcher") {
      return;
    }
    if (this.selectedIsStartPage) {
      if (this.mode != "start") {
        this.show("start");
      }
    } else if (this.mode == "start") {
      this.hide();
    }
  }

  openLauncher() {
    this._focusBefore = this.doc.activeElement;
    this.show("launcher");
  }

  closeLauncher({ restoreFocus = true } = {}) {
    if (this.mode != "launcher") {
      return;
    }
    this.hide();
    this.updateStartPage();
    if (!this.mode && restoreFocus) {
      this._focusBefore?.focus?.();
    }
    this._focusBefore = null;
  }

  // --- typing and choosing -------------------------------------------------------

  onKeyDown(e) {
    switch (e.key) {
      case "Enter":
        e.preventDefault();
        this.openSelected();
        break;
      case "ArrowDown":
      case "ArrowUp": {
        if (this.results.length) {
          e.preventDefault();
          let step = e.key == "ArrowDown" ? 1 : -1;
          this.select((this.selected + step + this.results.length) % this.results.length);
        }
        break;
      }
      case "Escape":
        e.preventDefault();
        e.stopPropagation();
        if (this.mode == "launcher") {
          this.closeLauncher();
        } else {
          this.input.value = "";
          this.renderResults([]);
        }
        break;
    }
  }

  scheduleSuggestions() {
    this.win.clearTimeout(this._suggestTimer);
    this._suggestTimer = this.win.setTimeout(() => this.suggest(), SUGGEST_DELAY_MS);
  }

  async suggest() {
    let text = this.input.value;
    let query = ++this._query;
    let results = [];
    let typed = resolveInput(text, { isPrivate: this.isPrivate });
    if (typed) {
      results.push({ ...typed, kind: typed.search ? "search" : "visit", text: text.trim() });
    }
    if (text.trim()) {
      try {
        let pages = await this.historyMatches(text.trim());
        if (query != this._query) {
          return; // superseded by later typing
        }
        for (let page of pages) {
          if (!results.some(r => r.url == page.url)) {
            results.push({ ...page, kind: "page" });
          }
        }
      } catch (e) {
        console.error(e);
      }
    }
    this.renderResults(results, text);
  }

  /**
   * Visited or bookmarked pages whose address or title contains the text,
   * most used first. (Frecency 0 marks pages Firefox never suggests; a new
   * visit can leave it at -1 until Firefox recalculates it.)
   */
  async historyMatches(text) {
    let { PlacesUtils } = ChromeUtils.importESModule("resource://gre/modules/PlacesUtils.sys.mjs");
    let db = await PlacesUtils.promiseDBConnection();
    // (Firefox rejects SQL with LIKE not followed by a bound parameter.)
    let pattern = `%${escapeLike(text)}%`;
    let rows = await db.executeCached(
      `SELECT url, title FROM moz_places
       WHERE hidden = 0 AND frecency <> 0
         AND (last_visit_date NOTNULL OR foreign_count > 0)
         AND (url LIKE :pattern ESCAPE '/' OR title LIKE :pattern ESCAPE '/')
         AND (substr(url, 1, 8) = 'https://' OR substr(url, 1, 7) = 'http://')
       ORDER BY frecency DESC, last_visit_date DESC
       LIMIT :limit`,
      { pattern, limit: MAX_HISTORY_RESULTS }
    );
    return rows.map(row => ({ url: row.getResultByName("url"), title: row.getResultByName("title") || "" }));
  }

  renderResults(results, text = "") {
    let { doc } = this;
    this.results = results;
    this.resultsFor = text;
    this.selected = 0;
    let rows = results.map((r, index) => {
      let row = html(doc, "li", {
        class: "evergreen-search-result",
        role: "option",
        "data-index": String(index),
        "data-kind": r.kind,
      });
      let label = html(doc, "span", { class: "evergreen-search-label" });
      let detail = html(doc, "span", { class: "evergreen-search-detail" });
      // Typed text, titles and addresses are set as text, never as markup.
      if (r.kind == "search") {
        label.textContent = r.text;
        if (this.engineName) {
          doc.l10n.setAttributes(detail, "evergreen-search-row-search", { engine: this.engineName });
        } else {
          doc.l10n.setAttributes(detail, "evergreen-search-row-search-no-engine");
        }
      } else if (r.kind == "visit") {
        label.textContent = r.url;
        doc.l10n.setAttributes(detail, "evergreen-search-row-visit");
      } else {
        label.textContent = r.title || r.url;
        detail.textContent = r.url;
      }
      row.append(label, detail);
      return row;
    });
    this.list.replaceChildren(...rows);
    this.input.setAttribute("aria-expanded", String(rows.length > 0));
    this.select(0);
  }

  select(index) {
    this.selected = index;
    for (let row of this.list.children) {
      let on = Number(row.dataset.index) == index;
      row.toggleAttribute("selected", on);
      row.setAttribute("aria-selected", String(on));
      if (on) {
        row.id = `evergreen-search-result-${index}`;
        this.input.setAttribute("aria-activedescendant", row.id);
      }
    }
    if (!this.results.length) {
      this.input.removeAttribute("aria-activedescendant");
    }
  }

  /** Open the selected result: in this tab (start page) or a new one. */
  openSelected() {
    let text = this.input.value;
    // Results lag typing slightly; Enter always means what is typed now.
    let current = this.resultsFor == text && this.results.length;
    let result = current ? this.results[this.selected] : resolveInput(text, { isPrivate: this.isPrivate });
    if (!result?.url) {
      return;
    }
    let where = this.mode == "launcher" ? "tab" : "current";
    this.hide();
    this.win.openTrustedLinkIn(result.url, where, { inBackground: false });
  }
}
