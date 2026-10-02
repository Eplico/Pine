/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

/**
 * Evergreen's default search engine: Ecosia, set once at first run.
 *
 * Google and DuckDuckGo stay available through Firefox's built-in engine list
 * (Settings > Search > Default Search Engine), and custom engines can be
 * added from the same page. Everything happens on the device: no Evergreen
 * service is involved.
 *
 * Firefox only ships Ecosia for some locales and regions, and that copy
 * carries Mozilla's partner code. Where it is missing, Evergreen adds its own
 * Ecosia entry with no partner code. Where Firefox already has one, that
 * entry is used, because two engines cannot share a name.
 */

export const ECOSIA = Object.freeze({
  name: "Ecosia",
  url: "https://www.ecosia.org/search?q={searchTerms}",
  suggestUrl: "https://ac.ecosia.org/autocomplete?q={searchTerms}&type=list",
  alias: "@ecosia",
});

/**
 * Find `def` by name, or add it as a user engine.
 *
 * Firefox changed the addUserEngine signature: older releases take
 * (name, url, alias); newer ones take a single form-info object.
 */
export async function ensureEngine(search, def) {
  let engine = search.getEngineByName(def.name);
  if (engine) {
    return { engine, added: false };
  }
  if (search.addUserEngine.length >= 2) {
    await search.addUserEngine(def.name, def.url, def.alias);
  } else {
    await search.addUserEngine({
      name: def.name,
      url: def.url,
      suggestUrl: def.suggestUrl,
      alias: def.alias,
    });
  }
  engine = search.getEngineByName(def.name);
  if (!engine) {
    throw new Error(`Could not add the ${def.name} search engine`);
  }
  return { engine, added: true };
}

/**
 * Make Ecosia the default engine and remember which engine that is, so
 * reconcileDefaultSearch can keep it the default. Returns { engine, added }.
 *
 * `managed` stores the id of the engine Evergreen made the default:
 * { get(): string, set(id), clear() }.
 */
export async function applyDefaultSearch(search, changeReason, managed = null) {
  let result = await ensureEngine(search, ECOSIA);
  await search.setDefault(result.engine, changeReason);
  managed?.set(result.engine.id);
  return result;
}

/**
 * Keep Ecosia the default after Firefox reloads its engine list, until the
 * user picks another engine.
 *
 * Firefox ships its own Ecosia in some regions, and it learns the region
 * after first run. When its Ecosia arrives, Firefox drops Evergreen's
 * same-named entry as a duplicate and, because that entry was the default,
 * falls back to its own default engine (Google): Ecosia is listed but no
 * longer the default. Here, if the engine Evergreen made the default has
 * disappeared and another Ecosia took its place, that one becomes the
 * default. If no Ecosia is left, the user removed it: stop managing.
 *
 * If that engine still exists but is not the default, the user chose another
 * engine: stop managing the default. Call this after Firefox has finished
 * changing engines (not synchronously from its notifications), because
 * Firefox announces the new default before it removes the duplicate.
 *
 * Returns true if it changed the default.
 */
export async function reconcileDefaultSearch(search, changeReason, managed) {
  let managedId = managed.get();
  if (!managedId) {
    return false;
  }
  if (search.getEngineById(managedId)) {
    if (search.defaultEngine?.id != managedId) {
      managed.clear();
    }
    return false;
  }
  let engine = search.getEngineByName(ECOSIA.name);
  if (!engine || engine.hidden) {
    // No Ecosia took its place: the user removed it. Leave the default alone.
    managed.clear();
    return false;
  }
  await search.setDefault(engine, changeReason);
  managed.set(engine.id);
  return true;
}
