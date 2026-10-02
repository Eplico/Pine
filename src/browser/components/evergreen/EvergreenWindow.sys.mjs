/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

/**
 * Evergreen's per-window UI: Spaces in the vertical-tabs sidebar, the Space
 * switcher, the Space editor, the Archive panel, and tab context-menu items.
 *
 * Security rules for this file (design doc §6.2): it runs with chrome
 * privileges, so web-supplied strings (titles, URLs) are only ever written as
 * text or attribute values, never parsed as markup; it creates no <browser>
 * elements; every page is an ordinary tabbrowser tab.
 *
 * Model:
 *  - Pinned tabs are Favorites, shared by every Space (Firefox cannot hide
 *    pinned tabs, and Arc's Favorites are shared too).
 *  - Every other tab belongs to exactly one Space, recorded on the tab via
 *    SessionStore so it survives restarts.
 *  - Switching Space hides the other Spaces' tabs (gBrowser.hideTab), which
 *    also takes them out of Ctrl+Tab and Ctrl+1..8.
 *  - A Space with separate sign-ins opens its tabs in its own container.
 */

import * as Spaces from "./Spaces.sys.mjs";
import { isArchivable, isRecordable, makeEntry } from "./Archive.sys.mjs";
import { ArchiveStore, archiveSettings } from "./ArchiveStore.sys.mjs";
import { SpacesStore } from "./SpacesStore.sys.mjs";

const HTML_NS = "http://www.w3.org/1999/xhtml";
const STYLESHEET = new URL("evergreen.css", import.meta.url).href;
const FTL = "browser/evergreen.ftl";

const TAB_SPACE = "evergreen-space"; // SessionStore tab value + tab attribute
const TAB_KEEP = "evergreen-keep"; // SessionStore tab value + tab attribute
const WINDOW_SPACE = "evergreen-active-space"; // SessionStore window value
const HIDDEN_BY = "evergreen";

// Space colour -> Firefox container colour.
const CONTAINER_COLORS = {
  green: "green",
  teal: "turquoise",
  blue: "blue",
  purple: "purple",
  pink: "pink",
  red: "red",
  orange: "orange",
  yellow: "yellow",
  gray: "toolbar",
};

const controllers = new Set();

export const EvergreenWindow = {
  /** Category entry point: browser-window-delayed-startup. */
  init(win) {
    if (!win.gBrowser || [...controllers].some(c => c.win == win)) {
      return;
    }
    let isPrivate = win.PrivateBrowsingUtils
      ? win.PrivateBrowsingUtils.isWindowPrivate(win)
      : win.document.documentElement.hasAttribute("privatebrowsingmode");
    if (isPrivate) {
      // Private windows get plain vertical tabs: no Spaces, nothing persisted.
      return;
    }
    let controller = new WindowController(win);
    controllers.add(controller);
    controller.start().catch(e => console.error("Evergreen window init failed", e));
  },

  /** Category entry point: browser-window-unload. */
  uninit(win) {
    for (let c of controllers) {
      if (c.win == win) {
        c.stop();
        controllers.delete(c);
      }
    }
  },

  controllerFor(win) {
    return [...controllers].find(c => c.win == win) ?? null;
  },

  /** Archive idle tabs in every window (called by EvergreenStartup's timer). */
  archiveIdleTabsEverywhere(now = Date.now()) {
    let entries = [];
    for (let c of controllers) {
      entries.push(...c.collectIdleTabs(now));
    }
    ArchiveStore.ready().then(() => ArchiveStore.add(entries));
    return entries.length;
  },

  /** Delete a Space everywhere: archive its tabs, then drop it. */
  async deleteSpace(spaceId) {
    let state = SpacesStore.state;
    let space = Spaces.getSpace(state, spaceId);
    if (!space || state.spaces.length == 1) {
      return;
    }
    let fallback = Spaces.fallbackSpaceId(state, spaceId);
    let entries = [];
    for (let c of controllers) {
      entries.push(...c.evacuateSpace(spaceId, fallback));
    }
    await ArchiveStore.ready();
    ArchiveStore.add(entries);
    SpacesStore.update(s => Spaces.removeSpace(s, spaceId));
    let win = [...controllers][0]?.win;
    if (
      win &&
      space.userContextId &&
      !Spaces.identityInUse(SpacesStore.state, space.userContextId)
    ) {
      // Removing the container also deletes its cookies and site data.
      win.ContextualIdentityService.remove(space.userContextId);
    }
  },
};

function el(doc, tag, attrs = {}) {
  let node = doc.createXULElement(tag);
  for (let [k, v] of Object.entries(attrs)) {
    node.setAttribute(k, v);
  }
  return node;
}

function html(doc, tag, attrs = {}) {
  let node = doc.createElementNS(HTML_NS, tag);
  for (let [k, v] of Object.entries(attrs)) {
    node.setAttribute(k, v);
  }
  return node;
}

class WindowController {
  constructor(win) {
    this.win = win;
    this.doc = win.document;
    this.gBrowser = win.gBrowser;
    this.activeSpaceId = null;
    this.lastSelected = new Map(); // spaceId -> tab
    this.defaultName = "Personal";
    this._switching = false;
    this._cleanups = [];
    this._editing = null; // { mode: "new" | "edit", spaceId }
  }

  // --- lifecycle ---------------------------------------------------------------

  async start() {
    await SpacesStore.ready();
    let { win, doc } = this;
    win.windowUtils.loadSheetUsingURIString(STYLESHEET, win.windowUtils.AUTHOR_SHEET);
    this._cleanups.push(() =>
      win.windowUtils.removeSheetUsingURIString(STYLESHEET, win.windowUtils.AUTHOR_SHEET)
    );
    win.MozXULElement.insertFTLIfNeeded(FTL);
    this.defaultName =
      (await doc.l10n.formatValue("evergreen-default-space-name")) || this.defaultName;

    this.buildSidebar();
    this.buildPopups();
    this.buildKeys();
    this.wrapNewTabLoading();

    let tabs = this.gBrowser.tabContainer;
    this.listen(tabs, "TabOpen", e => this.onTabOpen(e.target));
    this.listen(tabs, "TabSelect", e => this.onTabSelect(e.target));
    this.listen(tabs, "TabPinned", () => this.render());
    this.listen(tabs, "TabUnpinned", e => this.onTabUnpinned(e.target));
    this.listen(tabs, "TabShow", e => this.onTabShow(e.target));
    this.listen(tabs, "SSTabRestoring", e => this.onTabRestoring(e.target));
    this.listen(win, "SSWindowRestored", () => this.restoreWindowState());

    let onSpacesChanged = () => this.onSpacesChanged();
    SpacesStore.addListener(onSpacesChanged);
    this._cleanups.push(() => SpacesStore.removeListener(onSpacesChanged));

    this.restoreWindowState();
    this.expandSidebarOnFirstRun();
    doc.documentElement.setAttribute("evergreen", "true");
    this._cleanups.push(() => doc.documentElement.removeAttribute("evergreen"));
  }

  /**
   * Arc's sidebar starts expanded; Firefox's starts collapsed. Expand it once,
   * on the first run; after that Firefox remembers the user's choice.
   */
  expandSidebarOnFirstRun() {
    const PREF = "evergreen.sidebar.initialExpandDone";
    if (Services.prefs.getBoolPref(PREF, false)) {
      return;
    }
    let state = this.win.SidebarController?._state;
    if (state && "launcherExpanded" in state) {
      state.launcherExpanded = true;
    }
    Services.prefs.setBoolPref(PREF, true);
  }

  stop() {
    for (let fn of this._cleanups.reverse()) {
      try {
        fn();
      } catch (e) {
        console.error(e);
      }
    }
    this._cleanups = [];
  }

  listen(target, type, fn, options) {
    target.addEventListener(type, fn, options);
    this._cleanups.push(() => target.removeEventListener(type, fn, options));
  }

  // --- tab <-> Space bookkeeping ------------------------------------------------

  get state() {
    return SpacesStore.state;
  }

  activeSpace() {
    return Spaces.getSpace(this.state, this.activeSpaceId) ?? this.state.spaces[0];
  }

  displayName(space) {
    return space?.name || this.defaultName;
  }

  spaceOf(tab) {
    return tab.getAttribute(TAB_SPACE) || this.win.SessionStore.getCustomTabValue(tab, TAB_SPACE) || null;
  }

  setTabSpace(tab, spaceId) {
    tab.setAttribute(TAB_SPACE, spaceId);
    this.win.SessionStore.setCustomTabValue(tab, TAB_SPACE, spaceId);
  }

  isKept(tab) {
    return tab.hasAttribute(TAB_KEEP);
  }

  setKept(tab, kept) {
    tab.toggleAttribute(TAB_KEEP, kept);
    if (kept) {
      this.win.SessionStore.setCustomTabValue(tab, TAB_KEEP, "1");
    } else {
      this.win.SessionStore.deleteCustomTabValue(tab, TAB_KEEP);
    }
  }

  /** Make sure a tab's Space is valid, assigning `fallback` if it is not. */
  syncTab(tab, fallback) {
    if (this.win.SessionStore.getCustomTabValue(tab, TAB_KEEP)) {
      tab.setAttribute(TAB_KEEP, "true");
    }
    if (tab.pinned) {
      return;
    }
    let id = this.win.SessionStore.getCustomTabValue(tab, TAB_SPACE) || tab.getAttribute(TAB_SPACE);
    if (!Spaces.getSpace(this.state, id)) {
      id = fallback;
    }
    this.setTabSpace(tab, id);
  }

  restoreWindowState() {
    let saved = this.win.SessionStore.getCustomWindowValue(this.win, WINDOW_SPACE);
    let selected = this.gBrowser.selectedTab;
    let fallback = Spaces.getSpace(this.state, saved) ? saved : this.state.spaces[0].id;
    for (let tab of this.gBrowser.tabs) {
      this.syncTab(tab, fallback);
    }
    // The selected tab decides the Space, as in Arc.
    let active = (!selected.pinned && this.spaceOf(selected)) || fallback;
    this.applySpace(active, { selectTab: false });
  }

  onTabOpen(tab) {
    if (tab.pinned) {
      return;
    }
    // Restored tabs are corrected in onTabRestoring once their saved values
    // are available; everything else joins its opener's Space or the active one.
    let opener = tab.openerTab;
    let id = (opener && !opener.pinned && this.spaceOf(opener)) || this.activeSpace().id;
    this.setTabSpace(tab, id);
    this.render();
  }

  onTabRestoring(tab) {
    this.syncTab(tab, this.activeSpace().id);
    this.updateTabVisibility(tab);
  }

  onTabUnpinned(tab) {
    // A Favorite that is unpinned joins the Space it is being viewed in.
    this.setTabSpace(tab, this.activeSpace().id);
    this.render();
  }

  onTabShow(tab) {
    // Something else (an extension, "show all tabs") revealed a tab that
    // belongs to another Space: put it back.
    if (!this._switching && !tab.pinned && !tab.selected) {
      let id = this.spaceOf(tab);
      if (id && id != this.activeSpaceId) {
        this.gBrowser.hideTab(tab, HIDDEN_BY);
      }
    }
  }

  onTabSelect(tab) {
    if (tab.pinned) {
      return;
    }
    let id = this.spaceOf(tab);
    if (id) {
      this.lastSelected.set(id, tab);
    }
    if (!this._switching && id && id != this.activeSpaceId) {
      // Selecting a tab from another Space (from the urlbar's "Switch to
      // tab", for example) switches to that Space.
      this.applySpace(id, { selectTab: false });
    }
  }

  updateTabVisibility(tab) {
    if (tab.pinned) {
      return;
    }
    if (this.spaceOf(tab) == this.activeSpaceId || tab.selected) {
      this.gBrowser.showTab(tab);
    } else {
      this.gBrowser.hideTab(tab, HIDDEN_BY);
    }
  }

  // --- switching ------------------------------------------------------------------

  applySpace(spaceId, { selectTab = true } = {}) {
    let space = Spaces.getSpace(this.state, spaceId) ?? this.state.spaces[0];
    let gBrowser = this.gBrowser;
    this._switching = true;
    try {
      this.activeSpaceId = space.id;
      this.win.SessionStore.setCustomWindowValue(this.win, WINDOW_SPACE, space.id);
      let inSpace = t => !t.pinned && !t.closing && this.spaceOf(t) == space.id;
      let current = gBrowser.selectedTab;
      if (selectTab && !current.pinned && !inSpace(current)) {
        let target = this.lastSelected.get(space.id);
        if (!target || !target.isConnected || !inSpace(target)) {
          target = gBrowser.tabs
            .filter(inSpace)
            .sort((a, b) => (b.lastAccessed || 0) - (a.lastAccessed || 0))[0];
        }
        if (!target) {
          target = this.openTabInSpace(space);
        }
        gBrowser.showTab(target);
        gBrowser.selectedTab = target;
      }
      for (let tab of gBrowser.tabs) {
        if (tab.pinned) {
          continue;
        }
        if (inSpace(tab)) {
          gBrowser.showTab(tab);
        } else {
          gBrowser.hideTab(tab, HIDDEN_BY);
        }
      }
    } finally {
      this._switching = false;
    }
    this.render();
  }

  switchBy(delta) {
    this.applySpace(Spaces.neighborSpaceId(this.state, this.activeSpaceId, delta));
  }

  switchToIndex(index) {
    let space = this.state.spaces[index];
    if (space) {
      this.applySpace(space.id);
    }
  }

  openTabInSpace(space, url = this.win.BROWSER_NEW_TAB_URL) {
    let tab = this.gBrowser.addTrustedTab(url, {
      userContextId: space.userContextId,
      skipAnimation: true,
    });
    this.setTabSpace(tab, space.id);
    return tab;
  }

  /**
   * New tabs (Ctrl+T, the + button, bookmarks opened in a new tab) open in
   * the active Space's container. Prototype: this wraps the window's
   * openTrustedLinkIn; a real build turns it into a small hook patch.
   */
  wrapNewTabLoading() {
    let win = this.win;
    let original = win.openTrustedLinkIn;
    if (typeof original != "function") {
      return;
    }
    let controller = this;
    win.openTrustedLinkIn = function (url, where, params = {}) {
      if ((where == "tab" || where == "tabshifted") && params.userContextId === undefined) {
        let userContextId = controller.activeSpace().userContextId;
        if (userContextId) {
          params = { ...params, userContextId };
        }
      }
      return original.call(this, url, where, params);
    };
    this._cleanups.push(() => {
      win.openTrustedLinkIn = original;
    });
  }

  onSpacesChanged() {
    if (!Spaces.getSpace(this.state, this.activeSpaceId)) {
      this.applySpace(this.state.spaces[0].id);
      return;
    }
    this.render();
  }

  // --- Space management ---------------------------------------------------------

  createSpace({ name, color, separate }) {
    let userContextId = 0;
    if (separate) {
      let identity = this.win.ContextualIdentityService.create(
        Spaces.cleanName(name) || this.defaultName,
        "tree",
        CONTAINER_COLORS[color] ?? "green"
      );
      userContextId = identity.userContextId;
    }
    let created;
    SpacesStore.update(state => {
      let result = Spaces.addSpace(state, { name, color, userContextId });
      created = result.space;
      return result.state;
    });
    this.applySpace(created.id);
    return created;
  }

  editSpace(spaceId, { name, color }) {
    SpacesStore.update(state => Spaces.updateSpace(state, spaceId, { name, color }));
    let space = Spaces.getSpace(this.state, spaceId);
    if (space?.userContextId) {
      this.win.ContextualIdentityService.update(
        space.userContextId,
        this.displayName(space),
        "tree",
        CONTAINER_COLORS[space.color] ?? "green"
      );
    }
  }

  async confirmAndDeleteSpace(spaceId) {
    let space = Spaces.getSpace(this.state, spaceId);
    if (!space || this.state.spaces.length == 1) {
      return;
    }
    let count = this.gBrowser.tabs.filter(t => !t.pinned && this.spaceOf(t) == spaceId).length;
    let [title, message] = await this.doc.l10n.formatValues([
      { id: "evergreen-delete-space-title" },
      {
        id: space.userContextId
          ? "evergreen-delete-space-message-separate"
          : "evergreen-delete-space-message",
        args: { name: this.displayName(space), count },
      },
    ]);
    if (Services.prompt.confirm(this.win, title, message)) {
      await EvergreenWindow.deleteSpace(spaceId);
    }
  }

  /** Move this window off `spaceId` and archive its tabs. Returns archive entries. */
  evacuateSpace(spaceId, fallbackId) {
    if (this.activeSpaceId == spaceId) {
      this.applySpace(fallbackId);
    }
    let now = Date.now();
    let entries = [];
    for (let tab of [...this.gBrowser.tabs]) {
      if (!tab.pinned && this.spaceOf(tab) == spaceId) {
        let entry = this.entryForTab(tab, now);
        if (entry) {
          entries.push(entry);
        }
        this.gBrowser.removeTab(tab, { animate: false });
      }
    }
    return entries;
  }

  moveTabToSpace(tab, spaceId) {
    let target = Spaces.getSpace(this.state, spaceId);
    if (!target || tab.pinned) {
      return;
    }
    if ((tab.userContextId || 0) == target.userContextId) {
      this.setTabSpace(tab, target.id);
      if (tab.selected) {
        this.applySpace(target.id, { selectTab: false });
      } else {
        this.updateTabVisibility(tab);
      }
      return;
    }
    // A tab's container is fixed when it is created, so moving it to a Space
    // with different sign-ins reopens the page there.
    let url = tab.linkedBrowser.currentURI.spec;
    let wasSelected = tab.selected;
    let newTab = this.openTabInSpace(target, isRecordable(url) ? url : this.win.BROWSER_NEW_TAB_URL);
    if (wasSelected) {
      this.gBrowser.selectedTab = newTab;
    }
    this.gBrowser.removeTab(tab, { animate: false });
    if (wasSelected) {
      this.applySpace(target.id, { selectTab: false });
    } else {
      this.updateTabVisibility(newTab);
    }
  }

  // --- Archive ----------------------------------------------------------------------

  entryForTab(tab, now) {
    let url = tab.linkedBrowser?.currentURI?.spec;
    if (!isRecordable(url)) {
      return null;
    }
    return makeEntry(
      {
        url,
        title: tab.label,
        spaceId: this.spaceOf(tab),
        userContextId: tab.userContextId || 0,
      },
      now
    );
  }

  collectIdleTabs(now) {
    let settings = archiveSettings();
    let entries = [];
    for (let tab of [...this.gBrowser.tabs]) {
      let archivable = isArchivable(
        {
          pinned: tab.pinned,
          kept: this.isKept(tab),
          selected: tab.selected,
          soundPlaying: tab.soundPlaying,
          sharing: !!tab.linkedBrowser?._sharingState?.webRTC?.sharing,
          privateWindow: false,
          lastAccessed: tab.lastAccessed,
        },
        now,
        settings
      );
      if (!archivable) {
        continue;
      }
      let entry = this.entryForTab(tab, now);
      if (entry) {
        entries.push(entry);
      }
      this.gBrowser.removeTab(tab, { animate: false });
    }
    if (entries.length) {
      this.render();
    }
    return entries;
  }

  reopenArchived(entry) {
    if (!isRecordable(entry.url)) {
      return;
    }
    let space = Spaces.getSpace(this.state, entry.spaceId) ?? this.activeSpace();
    if (space.id != this.activeSpaceId) {
      this.applySpace(space.id, { selectTab: false });
    }
    let tab = this.openTabInSpace(space, entry.url);
    this.gBrowser.selectedTab = tab;
    ArchiveStore.remove(entry.id);
  }

  // --- UI: sidebar ------------------------------------------------------------------

  sidebarContainer() {
    // Firefox 157: #sidebar-container. Firefox 136: box#sidebar-main.
    return (
      this.doc.getElementById("sidebar-container") ??
      this.doc.querySelector("box#sidebar-main")
    );
  }

  buildSidebar() {
    let { doc } = this;
    let container = this.sidebarContainer();
    if (!container) {
      console.error("Evergreen: no sidebar container found; Spaces UI disabled");
      return;
    }

    let header = el(doc, "hbox", { id: "evergreen-space-header", align: "center" });
    let swatch = html(doc, "span", { class: "evergreen-space-swatch", "aria-hidden": "true" });
    let name = html(doc, "span", { id: "evergreen-space-name" });
    let menuButton = el(doc, "toolbarbutton", {
      id: "evergreen-space-menu-button",
      class: "evergreen-icon-button",
      "data-l10n-id": "evergreen-space-menu-button",
    });
    menuButton.addEventListener("command", () =>
      this.doc.getElementById("evergreen-space-menu").openPopup(menuButton, "after_end")
    );
    header.append(swatch, name, menuButton);

    let footer = el(doc, "hbox", { id: "evergreen-space-switcher", align: "center" });
    let archiveButton = el(doc, "toolbarbutton", {
      id: "evergreen-archive-button",
      class: "evergreen-icon-button",
      "data-l10n-id": "evergreen-archive-button",
    });
    archiveButton.addEventListener("command", () => this.openArchivePanel(archiveButton));
    let dots = el(doc, "hbox", {
      id: "evergreen-space-dots",
      flex: "1",
      pack: "center",
      align: "center",
      role: "tablist",
    });
    let newButton = el(doc, "toolbarbutton", {
      id: "evergreen-new-space-button",
      class: "evergreen-icon-button",
      "data-l10n-id": "evergreen-new-space-button",
    });
    newButton.addEventListener("command", () => this.openEditor(newButton, { mode: "new" }));
    footer.append(archiveButton, dots, newButton);

    container.prepend(header);
    container.append(footer);
    this._cleanups.push(() => {
      header.remove();
      footer.remove();
    });
  }

  render() {
    let { doc } = this;
    let space = this.activeSpace();
    let color = Spaces.spaceColor(space);
    doc.documentElement.style.setProperty("--evergreen-space-color", color);

    let name = doc.getElementById("evergreen-space-name");
    if (name) {
      name.textContent = this.displayName(space);
    }
    let dots = doc.getElementById("evergreen-space-dots");
    if (dots) {
      let buttons = this.state.spaces.map((s, i) => {
        let dot = el(doc, "toolbarbutton", {
          class: "evergreen-space-dot",
          role: "tab",
          "data-space-id": s.id,
          "aria-selected": String(s.id == space.id),
        });
        let key = i < 9 && doc.getElementById(`evergreen-key-space-${i + 1}`);
        let shortcut = key && this.win.ShortcutUtils?.prettifyShortcut(key);
        doc.l10n.setAttributes(
          dot,
          shortcut ? "evergreen-space-dot-with-shortcut" : "evergreen-space-dot",
          { name: this.displayName(s), shortcut: shortcut || "" }
        );
        dot.style.setProperty("--evergreen-dot-color", Spaces.spaceColor(s));
        dot.toggleAttribute("checked", s.id == space.id);
        dot.toggleAttribute("separate", !!s.userContextId);
        dot.addEventListener("command", () => this.applySpace(s.id));
        dot.addEventListener("contextmenu", e => {
          e.preventDefault();
          this.applySpace(s.id);
          doc.getElementById("evergreen-space-menu").openPopup(dot, "after_start");
        });
        return dot;
      });
      dots.replaceChildren(...buttons);
    }
    let menu = doc.getElementById("evergreen-space-menu-delete");
    if (menu) {
      menu.disabled = this.state.spaces.length == 1;
    }
  }

  // --- UI: popups -------------------------------------------------------------------

  buildPopups() {
    let { doc } = this;
    let popupSet = doc.getElementById("mainPopupSet");

    // Space menu.
    let spaceMenu = el(doc, "menupopup", { id: "evergreen-space-menu" });
    let editItem = el(doc, "menuitem", { "data-l10n-id": "evergreen-space-menu-edit" });
    editItem.addEventListener("command", () =>
      this.openEditor(doc.getElementById("evergreen-space-menu-button"), {
        mode: "edit",
        spaceId: this.activeSpaceId,
      })
    );
    let deleteItem = el(doc, "menuitem", {
      id: "evergreen-space-menu-delete",
      "data-l10n-id": "evergreen-space-menu-delete",
    });
    deleteItem.addEventListener("command", () => this.confirmAndDeleteSpace(this.activeSpaceId));
    spaceMenu.append(editItem, el(doc, "menuseparator"), deleteItem);

    let editor = this.buildEditor();
    let archive = this.buildArchivePanel();
    popupSet.append(spaceMenu, editor, archive);
    this._cleanups.push(() => {
      spaceMenu.remove();
      editor.remove();
      archive.remove();
    });

    this.buildTabContextMenu();
  }

  buildEditor() {
    let { doc } = this;
    let panel = el(doc, "panel", {
      id: "evergreen-space-editor",
      type: "arrow",
      role: "dialog",
      "aria-labelledby": "evergreen-editor-title",
    });
    let form = html(doc, "form", { class: "evergreen-panel evergreen-editor" });
    let title = html(doc, "h2", { id: "evergreen-editor-title" });
    let nameInput = html(doc, "input", {
      type: "text",
      id: "evergreen-editor-name",
      maxlength: String(Spaces.MAX_NAME_LENGTH),
      "data-l10n-id": "evergreen-editor-name",
    });
    let swatches = html(doc, "div", {
      class: "evergreen-swatches",
      role: "radiogroup",
      "data-l10n-id": "evergreen-editor-colors",
    });
    for (let colorName of Spaces.COLOR_NAMES) {
      let b = html(doc, "button", {
        type: "button",
        role: "radio",
        class: "evergreen-swatch",
        "data-color": colorName,
        "data-l10n-id": `evergreen-color-${colorName}`,
      });
      b.style.setProperty("--evergreen-swatch", Spaces.SPACE_COLORS[colorName]);
      b.addEventListener("click", () => this.selectSwatch(colorName));
      swatches.append(b);
    }
    let separateRow = html(doc, "label", { class: "evergreen-separate" });
    let separate = html(doc, "input", { type: "checkbox", id: "evergreen-editor-separate" });
    let separateLabel = html(doc, "span", { "data-l10n-id": "evergreen-editor-separate" });
    separateRow.append(separate, separateLabel);
    let hint = html(doc, "p", { id: "evergreen-editor-hint", class: "evergreen-hint" });
    let buttons = html(doc, "div", { class: "evergreen-buttons" });
    let cancel = html(doc, "button", { type: "button", "data-l10n-id": "evergreen-editor-cancel" });
    cancel.addEventListener("click", () => panel.hidePopup());
    let submit = html(doc, "button", {
      type: "submit",
      id: "evergreen-editor-submit",
      class: "primary",
    });
    buttons.append(cancel, submit);
    form.append(title, nameInput, swatches, separateRow, hint, buttons);
    form.addEventListener("submit", e => {
      e.preventDefault();
      this.submitEditor();
      panel.hidePopup();
    });
    separate.addEventListener("change", () => this.updateEditorHint());
    panel.addEventListener("popupshown", () => nameInput.focus());
    panel.append(form);
    return panel;
  }

  selectSwatch(colorName) {
    for (let b of this.doc.querySelectorAll("#evergreen-space-editor .evergreen-swatch")) {
      let on = b.dataset.color == colorName;
      b.setAttribute("aria-checked", String(on));
      b.toggleAttribute("selected", on);
    }
    this._editingColor = colorName;
  }

  updateEditorHint() {
    let doc = this.doc;
    let hint = doc.getElementById("evergreen-editor-hint");
    let separate = doc.getElementById("evergreen-editor-separate");
    let id;
    if (this._editing?.mode == "edit") {
      let space = Spaces.getSpace(this.state, this._editing.spaceId);
      id = space?.userContextId ? "evergreen-editor-identity-separate" : "evergreen-editor-identity-shared";
    } else {
      id = separate.checked ? "evergreen-editor-separate-hint" : "evergreen-editor-shared-hint";
    }
    doc.l10n.setAttributes(hint, id);
  }

  openEditor(anchor, { mode, spaceId = null }) {
    let doc = this.doc;
    this._editing = { mode, spaceId };
    let space = mode == "edit" ? Spaces.getSpace(this.state, spaceId) : null;
    doc.l10n.setAttributes(
      doc.getElementById("evergreen-editor-title"),
      mode == "edit" ? "evergreen-editor-title-edit" : "evergreen-editor-title-new"
    );
    doc.l10n.setAttributes(
      doc.getElementById("evergreen-editor-submit"),
      mode == "edit" ? "evergreen-editor-save" : "evergreen-editor-create"
    );
    doc.getElementById("evergreen-editor-name").value = space?.name ?? "";
    let separate = doc.getElementById("evergreen-editor-separate");
    separate.checked = false;
    separate.closest("label").hidden = mode == "edit";
    let unused = Spaces.COLOR_NAMES.find(c => !this.state.spaces.some(s => s.color == c));
    this.selectSwatch(space?.color ?? unused ?? "green");
    this.updateEditorHint();
    doc.getElementById("evergreen-space-editor").openPopup(anchor, "after_start");
  }

  submitEditor() {
    let doc = this.doc;
    let name = doc.getElementById("evergreen-editor-name").value;
    let color = this._editingColor;
    if (this._editing?.mode == "edit") {
      this.editSpace(this._editing.spaceId, { name, color });
    } else {
      let separate = doc.getElementById("evergreen-editor-separate").checked;
      this.createSpace({ name, color, separate });
    }
    this._editing = null;
  }

  buildArchivePanel() {
    let { doc } = this;
    let panel = el(doc, "panel", {
      id: "evergreen-archive-panel",
      type: "arrow",
      role: "dialog",
      "aria-labelledby": "evergreen-archive-title",
    });
    let box = html(doc, "div", { class: "evergreen-panel evergreen-archive" });
    let title = html(doc, "h2", {
      id: "evergreen-archive-title",
      "data-l10n-id": "evergreen-archive-title",
    });
    let empty = html(doc, "p", { id: "evergreen-archive-empty", class: "evergreen-hint" });
    let list = html(doc, "ul", { id: "evergreen-archive-list" });
    let clear = html(doc, "button", {
      type: "button",
      id: "evergreen-archive-clear",
      "data-l10n-id": "evergreen-archive-clear",
    });
    clear.addEventListener("click", () => {
      ArchiveStore.clear();
      this.renderArchive();
    });
    box.append(title, empty, list, clear);
    panel.append(box);
    return panel;
  }

  async openArchivePanel(anchor) {
    await ArchiveStore.ready();
    this.renderArchive();
    this.doc.getElementById("evergreen-archive-panel").openPopup(anchor, "before_start");
  }

  renderArchive() {
    let { doc } = this;
    let entries = ArchiveStore.entries;
    let empty = doc.getElementById("evergreen-archive-empty");
    doc.l10n.setAttributes(empty, "evergreen-archive-empty", {
      hours: archiveSettings().afterHours,
    });
    empty.hidden = entries.length > 0;
    doc.getElementById("evergreen-archive-clear").hidden = !entries.length;
    let items = entries.slice(0, 100).map(entry => {
      let li = html(doc, "li");
      let button = html(doc, "button", { type: "button", class: "evergreen-archive-item" });
      let icon = html(doc, "img", { alt: "", role: "presentation" });
      icon.src = "page-icon:" + entry.url;
      let text = html(doc, "span", { class: "evergreen-archive-text" });
      let title = html(doc, "span", { class: "evergreen-archive-entry-title" });
      title.textContent = entry.title || entry.url;
      let host = html(doc, "span", { class: "evergreen-archive-entry-host" });
      try {
        host.textContent = new URL(entry.url).host;
      } catch {
        host.textContent = "";
      }
      text.append(title, host);
      button.append(icon, text);
      button.title = entry.url;
      button.addEventListener("click", () => {
        this.reopenArchived(entry);
        doc.getElementById("evergreen-archive-panel").hidePopup();
      });
      li.append(button);
      return li;
    });
    doc.getElementById("evergreen-archive-list").replaceChildren(...items);
  }

  buildTabContextMenu() {
    let { doc } = this;
    let menu = doc.getElementById("tabContextMenu");
    if (!menu) {
      return;
    }
    let separator = el(doc, "menuseparator", { id: "evergreen-tab-separator" });
    let move = el(doc, "menu", {
      id: "evergreen-tab-move",
      "data-l10n-id": "evergreen-tab-move-to-space",
    });
    let movePopup = el(doc, "menupopup", { id: "evergreen-tab-move-popup" });
    move.append(movePopup);
    let keep = el(doc, "menuitem", {
      id: "evergreen-tab-keep",
      type: "checkbox",
      "data-l10n-id": "evergreen-tab-keep",
    });
    menu.append(separator, move, keep);

    let contextTab = () => {
      let node = menu.triggerNode;
      return (node && node.closest?.("tab")) || this.win.TabContextMenu?.contextTab || this.gBrowser.selectedTab;
    };
    let onShowing = e => {
      if (e.target != menu) {
        return;
      }
      let tab = contextTab();
      let hidden = !tab || tab.pinned;
      separator.hidden = move.hidden = keep.hidden = hidden;
      if (hidden) {
        return;
      }
      keep.setAttribute("checked", String(this.isKept(tab)));
      let current = this.spaceOf(tab);
      let items = this.state.spaces
        .filter(s => s.id != current)
        .map(s => {
          let item = el(doc, "menuitem", { label: this.displayName(s) });
          item.addEventListener("command", () => this.moveTabToSpace(tab, s.id));
          return item;
        });
      movePopup.replaceChildren(...items);
      move.disabled = !items.length;
    };
    keep.addEventListener("command", () => {
      let tab = contextTab();
      if (tab) {
        this.setKept(tab, !this.isKept(tab));
      }
    });
    this.listen(menu, "popupshowing", onShowing);
    this._cleanups.push(() => {
      separator.remove();
      move.remove();
      keep.remove();
    });
  }

  buildKeys() {
    let { doc } = this;
    let keyset = el(doc, "keyset", { id: "evergreen-keyset" });
    for (let i = 1; i <= 9; i++) {
      // keycode (not key) so the shortcut works whatever Shift+digit types on
      // the keyboard layout; keycode-based keys must listen to keydown, as
      // Firefox's own extension shortcuts do.
      let key = el(doc, "key", {
        id: `evergreen-key-space-${i}`,
        keycode: `VK_${i}`,
        modifiers: "accel,shift",
        event: "keydown",
      });
      key.addEventListener("command", () => this.switchToIndex(i - 1));
      keyset.append(key);
    }
    doc.documentElement.append(keyset);
    this._cleanups.push(() => keyset.remove());
  }
}
