/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

/**
 * The Spaces data model: pure functions over plain objects, with no Firefox
 * dependencies, so the rules can be unit-tested outside the browser.
 *
 * State shape (persisted by SpacesStore as evergreen/spaces.json):
 *   { version: 1, spaces: [Space, ...] }
 * Space:
 *   { id, name, color, userContextId, createdAt }
 * An empty name means "use the localized default name".
 * userContextId is the Firefox container ("sign-in identity") the Space's
 * tabs open in; 0 is the default identity shared with normal browsing.
 */

export const STATE_VERSION = 1;
export const MAX_NAME_LENGTH = 40;
export const MAX_SPACES = 24;

export const SPACE_COLORS = Object.freeze({
  green: "#2ea060",
  teal: "#0d9488",
  blue: "#3b82f6",
  purple: "#8b5cf6",
  pink: "#db2777",
  red: "#dc2626",
  orange: "#ea580c",
  yellow: "#ca8a04",
  gray: "#64748b",
});
export const COLOR_NAMES = Object.freeze(Object.keys(SPACE_COLORS));

export function defaultState(now = Date.now()) {
  return {
    version: STATE_VERSION,
    spaces: [
      { id: "personal", name: "", color: "green", userContextId: 0, createdAt: now },
    ],
  };
}

export function makeSpaceId(random = Math.random) {
  return "s-" + Math.floor(random() * 36 ** 8).toString(36).padStart(8, "0");
}

export function cleanName(name) {
  return String(name ?? "")
    // Control characters are deliberately matched: they are stripped.
    // eslint-disable-next-line no-control-regex
    .replace(/[\u0000-\u001f\u007f]/g, "")
    .trim()
    .slice(0, MAX_NAME_LENGTH);
}

function cleanSpace(raw, now) {
  if (!raw || typeof raw != "object" || typeof raw.id != "string" || !raw.id) {
    return null;
  }
  let userContextId = Number.isInteger(raw.userContextId) ? raw.userContextId : 0;
  return {
    id: raw.id.slice(0, 64),
    name: cleanName(raw.name),
    color: COLOR_NAMES.includes(raw.color) ? raw.color : "green",
    userContextId: userContextId >= 0 ? userContextId : 0,
    createdAt: Number.isFinite(raw.createdAt) ? raw.createdAt : now,
  };
}

/**
 * Validate state read from disk (or produced by an update). Invalid spaces
 * are dropped, duplicate ids are removed, and there is always at least one
 * Space.
 */
export function normalizeState(raw, now = Date.now()) {
  let spaces = [];
  let ids = new Set();
  for (let candidate of Array.isArray(raw?.spaces) ? raw.spaces : []) {
    let space = cleanSpace(candidate, now);
    if (space && !ids.has(space.id) && spaces.length < MAX_SPACES) {
      ids.add(space.id);
      spaces.push(space);
    }
  }
  if (!spaces.length) {
    return defaultState(now);
  }
  return { version: STATE_VERSION, spaces };
}

export function getSpace(state, id) {
  return state.spaces.find(s => s.id == id) ?? null;
}

export function spaceIndex(state, id) {
  return state.spaces.findIndex(s => s.id == id);
}

export function spaceColor(space) {
  return SPACE_COLORS[space?.color] ?? SPACE_COLORS.green;
}

/**
 * Returns { state, space } with a new Space appended.
 * `userContextId` must already exist (the caller creates containers).
 */
export function addSpace(state, { name, color, userContextId = 0 }, options = {}) {
  if (state.spaces.length >= MAX_SPACES) {
    throw new Error(`Evergreen supports at most ${MAX_SPACES} Spaces`);
  }
  let { idFactory = makeSpaceId, now = Date.now() } = options;
  let id;
  do {
    id = idFactory();
  } while (getSpace(state, id));
  let space = cleanSpace({ id, name, color, userContextId, createdAt: now }, now);
  return { state: { ...state, spaces: [...state.spaces, space] }, space };
}

/** Only name and color can change; a Space's identity is fixed. */
export function updateSpace(state, id, { name, color } = {}) {
  let found = false;
  let spaces = state.spaces.map(s => {
    if (s.id != id) {
      return s;
    }
    found = true;
    return {
      ...s,
      name: name === undefined ? s.name : cleanName(name),
      color: COLOR_NAMES.includes(color) ? color : s.color,
    };
  });
  if (!found) {
    throw new Error(`No Space with id ${id}`);
  }
  return { ...state, spaces };
}

export function removeSpace(state, id) {
  if (!getSpace(state, id)) {
    throw new Error(`No Space with id ${id}`);
  }
  if (state.spaces.length == 1) {
    throw new Error("The last Space cannot be deleted");
  }
  return { ...state, spaces: state.spaces.filter(s => s.id != id) };
}

export function moveSpace(state, id, newIndex) {
  let from = spaceIndex(state, id);
  if (from == -1) {
    throw new Error(`No Space with id ${id}`);
  }
  let spaces = [...state.spaces];
  let [space] = spaces.splice(from, 1);
  let to = Math.max(0, Math.min(newIndex, spaces.length));
  spaces.splice(to, 0, space);
  return { ...state, spaces };
}

/** The Space `delta` steps away from `id`, wrapping around. */
export function neighborSpaceId(state, id, delta) {
  let count = state.spaces.length;
  let from = Math.max(0, spaceIndex(state, id));
  return state.spaces[(((from + delta) % count) + count) % count].id;
}

/** Where to go when Space `id` is removed: the previous one, else the next. */
export function fallbackSpaceId(state, id) {
  let i = spaceIndex(state, id);
  let others = state.spaces.filter(s => s.id != id);
  if (!others.length) {
    return null;
  }
  return others[Math.max(0, i - 1)]?.id ?? others[0].id;
}

/** Is a container used by any Space other than `exceptId`? */
export function identityInUse(state, userContextId, exceptId = null) {
  return state.spaces.some(s => s.id != exceptId && s.userContextId == userContextId);
}
