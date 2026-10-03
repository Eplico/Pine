/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

/**
 * Evergreen's window layout around Firefox's toolbar and sidebar:
 *
 *  - The toolbar starts with the app menu (an evergreen tree), the sidebar
 *    button and Downloads. The navigation buttons and address bar that follow
 *    line up with the left edge of the page, so they move right when the
 *    sidebar is open and sit right after Downloads when it is collapsed.
 *    Extensions stay at the far right.
 *  - The sidebar button collapses the sidebar completely. While collapsed,
 *    moving the mouse to the left edge of the window slides the sidebar in
 *    over the page (the page does not resize), and it slides away again when
 *    the mouse leaves it.
 *
 * Firefox's own sidebar code still runs the sidebar (tabs, tools, resizing);
 * Evergreen keeps it expanded and moves it out of the layout while collapsed.
 */

const COLLAPSED_PREF = "evergreen.sidebar.collapsed"; // last choice, for new windows
const WINDOW_COLLAPSED = "evergreen-sidebar-collapsed"; // SessionStore window value
// Just long enough to forgive a mouse that grazes the edge on its way past.
const PEEK_HIDE_DELAY_MS = 40;

/**
 * The toolbar order, applied once per profile (EvergreenStartup): the sidebar
 * button and Downloads first, then the navigation buttons and an address bar
 * that fills the space (no springs centring it), then everything else.
 * The app menu is moved in front of them in each window (WindowLayout).
 */
export function applyToolbarLayout(CustomizableUI) {
  const NAV = CustomizableUI.AREA_NAVBAR;
  for (let id of ["sidebar-button", "downloads-button"]) {
    if (CustomizableUI.getPlacementOfWidget(id)?.area != NAV) {
      CustomizableUI.addWidgetToArea(id, NAV);
    }
  }
  for (let id of CustomizableUI.getWidgetIdsInArea(NAV)) {
    if (CustomizableUI.isSpecialWidget(id) && id.includes("spring")) {
      CustomizableUI.removeWidgetFromArea(id);
    }
  }
  // Put these first, in this order, whatever order Firefox started with.
  // Everything else (Firefox View, the tab list, the window-drag spacer,
  // the account button) keeps its order after the address bar.
  let front = [
    "sidebar-button",
    "downloads-button",
    "back-button",
    "forward-button",
    "stop-reload-button",
    "home-button",
    "urlbar-container",
  ].filter(id => CustomizableUI.getPlacementOfWidget(id)?.area == NAV);
  front.forEach((id, index) => CustomizableUI.moveWidgetWithinArea(id, index));
}

export class WindowLayout {
  constructor(win) {
    this.win = win;
    this.doc = win.document;
    this.collapsed = false;
    this._cleanups = [];
    this._hideTimer = null;
    this._alignFrame = 0;
  }

  start() {
    this.moveAppMenu();
    this.alignNavigation();
    this.setUpSidebar();
  }

  stop() {
    this.win.clearTimeout(this._hideTimer);
    this.win.cancelAnimationFrame(this._alignFrame);
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

  // --- Toolbar ---------------------------------------------------------------------

  /** The app menu (Firefox's ☰, drawn as a tree) goes first in the toolbar. */
  moveAppMenu() {
    let { doc } = this;
    let navBar = doc.getElementById("nav-bar");
    let menu = doc.getElementById("PanelUI-button");
    let target = doc.getElementById("nav-bar-customization-target");
    if (!navBar || !menu || !target || target.parentNode != navBar) {
      return;
    }
    let next = menu.nextSibling;
    navBar.insertBefore(menu, target);
    this._cleanups.push(() => navBar.insertBefore(menu, next?.parentNode == navBar ? next : null));
  }

  /**
   * Keep the first toolbar item after Downloads lined up with the page's left
   * edge, by giving it a start margin.
   */
  alignNavigation() {
    let { doc, win } = this;
    let tabbox = doc.getElementById("tabbrowser-tabbox");
    let navBar = doc.getElementById("nav-bar");
    if (!tabbox || !navBar) {
      return;
    }
    let update = () => {
      win.cancelAnimationFrame(this._alignFrame);
      this._alignFrame = win.requestAnimationFrame(() => this.updateAlignment());
    };
    // The page's size follows the sidebar; the toolbar's follows the window
    // and customization; the aligned item's goes from 0 when it first renders.
    let observer = (this._observer = new win.ResizeObserver(update));
    observer.observe(tabbox);
    observer.observe(navBar);
    let downloads = doc.getElementById("downloads-button");
    if (downloads) {
      observer.observe(downloads);
    }
    this._cleanups.push(() => observer.disconnect());
    this.listen(win, "aftercustomization", update);
    this.listen(win, "resize", update);
    this._cleanups.push(() => {
      this.alignedItem()?.style.removeProperty("margin-inline-start");
      navBar.removeAttribute("evergreen-aligned");
    });
    update();
  }

  /** The first visible toolbar item after Downloads, or null. */
  alignedItem() {
    let downloads = this.doc.getElementById("downloads-button");
    if (!downloads || downloads.parentNode?.id != "nav-bar-customization-target") {
      return null;
    }
    let item = downloads.nextElementSibling;
    while (item && (item.hidden || item.collapsed || !item.getBoundingClientRect().width)) {
      item = item.nextElementSibling;
    }
    return item;
  }

  updateAlignment() {
    let { doc, win } = this;
    let item = this.alignedItem();
    if (this._aligned != item) {
      this._aligned?.style.removeProperty("margin-inline-start");
      if (this._aligned) {
        this._observer?.unobserve(this._aligned);
      }
      if (item) {
        this._observer?.observe(item);
      }
      this._aligned = item;
    }
    if (!item || doc.documentElement.hasAttribute("customizing")) {
      item?.style.removeProperty("margin-inline-start");
      return;
    }
    // Measure from the end of the button before it (Downloads), which does
    // not move while the margin animates.
    let prev = item.previousElementSibling;
    if (!prev) {
      return;
    }
    let page = doc.getElementById("tabbrowser-tabbox").getBoundingClientRect();
    let anchor = prev.getBoundingClientRect();
    let rtl = win.getComputedStyle(item).direction == "rtl";
    let offset = Math.round(Math.max(0, rtl ? anchor.left - page.right : page.left - anchor.right));
    if (parseFloat(item.style.marginInlineStart) !== offset) {
      item.style.setProperty("margin-inline-start", `${offset}px`);
    }
    // Animate later changes (the sidebar opening and closing), not the first one.
    let navBar = doc.getElementById("nav-bar");
    if (!navBar.hasAttribute("evergreen-aligned")) {
      win.requestAnimationFrame(() => navBar.setAttribute("evergreen-aligned", "true"));
    }
  }

  // --- Sidebar ---------------------------------------------------------------------

  get sidebarContainer() {
    // Firefox 157: #sidebar-container. Firefox 136: box#sidebar-main.
    return this.doc.getElementById("sidebar-container") ?? this.doc.querySelector("box#sidebar-main");
  }

  setUpSidebar() {
    let { win, doc } = this;
    let controller = win.SidebarController;
    let container = this.sidebarContainer;
    let browser = doc.getElementById("browser");
    if (!controller || !container || !browser) {
      return;
    }

    // The sidebar button (and its keyboard shortcut) collapse and expand.
    let originalClick = controller.handleToolbarButtonClick;
    controller.handleToolbarButtonClick = () => this.setCollapsed(!this.collapsed);
    let originalUpdate = controller.updateToolbarButton;
    controller.updateToolbarButton = (...args) => {
      let result = originalUpdate.apply(controller, args);
      this.updateButton();
      return result;
    };
    this._cleanups.push(() => {
      controller.handleToolbarButtonClick = originalClick;
      controller.updateToolbarButton = originalUpdate;
    });

    // The strip along the left edge that reveals a collapsed sidebar.
    let edge = doc.createXULElement("box");
    edge.id = "evergreen-sidebar-edge";
    browser.append(edge);
    this._cleanups.push(() => edge.remove());
    this.listen(edge, "mouseenter", () => this.peek());
    this.listen(edge, "dragenter", () => this.peek());

    this.listen(container, "mouseenter", () => this.win.clearTimeout(this._hideTimer));
    this.listen(container, "mouseleave", () => this.scheduleUnpeek());
    // A menu or panel opened from the sidebar keeps it out until it closes.
    this.listen(doc, "popuphidden", () => {
      if (this.peeking) {
        this.scheduleUnpeek();
      }
    });
    let restored = this.savedCollapsed();
    let collapsed = restored ?? Services.prefs.getBoolPref(COLLAPSED_PREF, false);
    this.listen(win, "SSWindowRestored", () => {
      let value = this.savedCollapsed();
      if (value != null) {
        this.setCollapsed(value, { remember: false });
      }
    });
    this.setCollapsed(collapsed, { remember: false });
    this._cleanups.push(() => {
      doc.documentElement.removeAttribute("evergreen-sidebar-collapsed");
      doc.documentElement.removeAttribute("evergreen-sidebar-peek");
    });
  }

  /** This window's saved choice (session restore), or null. */
  savedCollapsed() {
    try {
      let value = this.win.SessionStore?.getCustomWindowValue(this.win, WINDOW_COLLAPSED);
      return value ? value == "true" : null;
    } catch {
      return null; // not tracked by SessionStore yet
    }
  }

  get peeking() {
    return this.doc.documentElement.hasAttribute("evergreen-sidebar-peek");
  }

  setCollapsed(collapsed, { remember = true } = {}) {
    let { win, doc } = this;
    this.collapsed = collapsed;
    this.win.clearTimeout(this._hideTimer);
    doc.documentElement.removeAttribute("evergreen-sidebar-peek");
    doc.documentElement.toggleAttribute("evergreen-sidebar-collapsed", collapsed);
    if (!collapsed) {
      // Firefox's launcher must be expanded for Evergreen's sidebar.
      let state = win.SidebarController?._state;
      if (state && "launcherExpanded" in state && !state.launcherExpanded) {
        state.launcherExpanded = true;
      }
    }
    if (remember) {
      Services.prefs.setBoolPref(COLLAPSED_PREF, collapsed);
      try {
        win.SessionStore?.setCustomWindowValue(win, WINDOW_COLLAPSED, String(collapsed));
      } catch (e) {
        console.error(e);
      }
    }
    this.updateButton();
    this.updateAlignment();
  }

  updateButton() {
    let button = this.doc.getElementById("sidebar-button");
    if (!button) {
      return;
    }
    let key = this.doc.getElementById("toggleSidebarKb");
    let shortcut = (key && this.win.ShortcutUtils?.prettifyShortcut(key)) || "";
    button.checked = !this.collapsed;
    this.doc.l10n.setAttributes(
      button,
      this.collapsed ? "evergreen-sidebar-button-show" : "evergreen-sidebar-button-hide",
      { shortcut }
    );
  }

  peek() {
    if (!this.collapsed) {
      return;
    }
    this.win.clearTimeout(this._hideTimer);
    this.doc.documentElement.setAttribute("evergreen-sidebar-peek", "true");
  }

  scheduleUnpeek() {
    this.win.clearTimeout(this._hideTimer);
    if (!this.peeking) {
      return;
    }
    this._hideTimer = this.win.setTimeout(() => this.unpeekUnlessBusy(), PEEK_HIDE_DELAY_MS);
  }

  unpeekUnlessBusy() {
    let container = this.sidebarContainer;
    if (!this.peeking || container.matches(":hover")) {
      return;
    }
    // Wait while a menu or panel is open (a tab's context menu, the Space
    // editor) or something is being dragged; popuphidden checks again.
    let popupOpen = [...this.doc.querySelectorAll("menupopup, panel")].some(p =>
      ["open", "showing"].includes(p.state)
    );
    let dragging = false;
    try {
      let dragService = Cc["@mozilla.org/widget/dragservice;1"].getService(Ci.nsIDragService);
      dragging = !!dragService.getCurrentSession(this.win);
    } catch {
      // No drag service: nothing is being dragged.
    }
    // Renaming a Space in the sidebar also keeps it out.
    if (popupOpen || this.doc.getElementById("evergreen-space-name-input")) {
      return;
    }
    if (dragging) {
      this.scheduleUnpeek();
      return;
    }
    this.doc.documentElement.removeAttribute("evergreen-sidebar-peek");
  }
}
