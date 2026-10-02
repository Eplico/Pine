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

/** Make Ecosia the default engine. Returns { engine, added }. */
export async function applyDefaultSearch(search, changeReason) {
  let result = await ensureEngine(search, ECOSIA);
  await search.setDefault(result.engine, changeReason);
  return result;
}
