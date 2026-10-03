/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

import assert from "node:assert/strict";
import { test } from "node:test";

// The fixup flags resolveInput passes, as Firefox defines them.
globalThis.Ci = {
  nsIURIFixup: {
    FIXUP_FLAG_ALLOW_KEYWORD_LOOKUP: 1,
    FIXUP_FLAG_FIX_SCHEME_TYPOS: 8,
    FIXUP_FLAG_PRIVATE_CONTEXT: 16,
  },
};

const { escapeLike, isStartPage, resolveInput } = await import(
  "../../src/browser/components/evergreen/EvergreenSearch.sys.mjs"
);

/** A fake of Services.uriFixup: words search, anything with a dot is a site. */
function fakeFixup() {
  let calls = [];
  return {
    calls,
    getFixupURIInfo(text, flags) {
      calls.push([text, flags]);
      if (text.startsWith("throw")) {
        throw new Error("NS_ERROR_MALFORMED_URI");
      }
      let scheme = text.match(/^([a-z-]+):/)?.[1];
      if (scheme) {
        return { preferredURI: { scheme, spec: text }, keywordProviderName: "" };
      }
      if (text.includes(".")) {
        return { preferredURI: { scheme: "https", spec: `https://${text}/` }, keywordProviderName: "" };
      }
      let engine = flags & 16 ? "DuckDuckGo" : "Ecosia";
      return {
        preferredURI: { scheme: "https", spec: `https://search.example/?q=${encodeURIComponent(text)}` },
        keywordProviderName: engine,
      };
    },
  };
}

test("words search with the default engine", () => {
  let uriFixup = fakeFixup();
  assert.deepEqual(resolveInput("  green trees ", { uriFixup }), {
    url: "https://search.example/?q=green%20trees",
    search: true,
  });
  assert.deepEqual(uriFixup.calls, [["green trees", 1 | 8]]);
});

test("addresses open", () => {
  let uriFixup = fakeFixup();
  assert.deepEqual(resolveInput("example.com", { uriFixup }), { url: "https://example.com/", search: false });
  assert.deepEqual(resolveInput("about:robots", { uriFixup }), { url: "about:robots", search: false });
});

test("private windows ask for the private-browsing engine", () => {
  let uriFixup = fakeFixup();
  resolveInput("trees", { uriFixup, isPrivate: true });
  assert.equal(uriFixup.calls[0][1] & 16, 16);
});

test("script and other schemes never open", () => {
  let uriFixup = fakeFixup();
  assert.equal(resolveInput("javascript:alert(1)", { uriFixup }), null);
  assert.equal(resolveInput("data:text/html,hi", { uriFixup }), null);
  assert.equal(resolveInput("chrome://browser/content/browser.xhtml", { uriFixup }), null);
});

test("empty or unparseable text opens nothing", () => {
  let uriFixup = fakeFixup();
  assert.equal(resolveInput("   ", { uriFixup }), null);
  assert.equal(resolveInput("throw me", { uriFixup }), null);
  assert.equal(uriFixup.calls.length, 1); // blank text is not looked up
});

test("start pages are Firefox's home and new-tab pages", () => {
  assert.ok(isStartPage("about:home"));
  assert.ok(isStartPage("about:newtab"));
  assert.ok(!isStartPage("about:blank"));
  assert.ok(!isStartPage("https://example.com/"));
});

test("LIKE patterns escape their wildcards", () => {
  assert.equal(escapeLike("100%_sure/ok"), "100/%/_sure//ok");
  assert.equal(escapeLike("plain"), "plain");
});
