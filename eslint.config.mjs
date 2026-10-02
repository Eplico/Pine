/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

import js from "@eslint/js";
import noUnsanitized from "eslint-plugin-no-unsanitized";
import globals from "globals";

// Globals available to privileged (chrome) code in Firefox system modules.
const firefoxChrome = {
  Cc: "readonly",
  Ci: "readonly",
  Cu: "readonly",
  ChromeUtils: "readonly",
  Components: "readonly",
  IOUtils: "readonly",
  L10nFileSource: "readonly",
  L10nRegistry: "readonly",
  PathUtils: "readonly",
  Services: "readonly",
};

export default [
  {
    // Pref files use Firefox's pref-file syntax, not JavaScript modules.
    ignores: ["node_modules/", ".eg/", "prefs/", "branding/", "dev/autoconfig.js"],
  },
  js.configs.recommended,
  {
    // Evergreen's privileged UI code (design doc §6.2).
    files: ["src/**/*.mjs", "dev/**/*.mjs"],
    plugins: { "no-unsanitized": noUnsanitized },
    languageOptions: {
      ecmaVersion: "latest",
      sourceType: "module",
      globals: { ...globals.browser, ...firefoxChrome },
    },
    rules: {
      // Never turn web-supplied strings into markup.
      "no-unsanitized/method": "error",
      "no-unsanitized/property": "error",
      "no-eval": "error",
      "no-implied-eval": "error",
      "no-new-func": "error",
      "no-script-url": "error",
      "no-unused-vars": ["error", { args: "none" }],
      "prefer-const": "off",
    },
  },
  {
    files: ["tests/**/*.mjs", "eslint.config.mjs"],
    languageOptions: {
      ecmaVersion: "latest",
      sourceType: "module",
      globals: globals.node,
    },
  },
];
