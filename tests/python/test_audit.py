# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

"""Tests for the prefs audit (tools/eg/audit.py). Run: python -m unittest discover tests/python"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tools"))

from eg import audit  # noqa: E402

ARKENFOX = '''
/*** [SECTION 0100]: STARTUP ***/
user_pref("browser.startup.page", 0);
user_pref("_user.js.parrot", "0100 syntax error");
/* user_pref("commented.out", true); */
// user_pref("also.commented", true);
/** NETWORK ***/
user_pref("network.thing", true);
user_pref("network.number", 2);
user_pref("network.text", "x");
user_pref("evergreen.same", true);
'''

STATIC = """
- name: network.thing
  type: RelaxedAtomicBool
  value: false
  mirror: always

- name: network.number
  type: int32_t
  value: 2

- name: network.build
  type: bool
  value: @IS_NIGHTLY_BUILD@
"""

FIREFOX_JS = 'pref("network.text", "y");\npref("browser.startup.page", 1);\n'


class AuditTests(unittest.TestCase):
    def test_parses_active_prefs_only(self):
        rows = audit.parse_arkenfox(ARKENFOX)
        names = [r[0] for r in rows]
        self.assertIn("browser.startup.page", names)
        self.assertNotIn("commented.out", names)
        self.assertNotIn("also.commented", names)
        self.assertEqual(dict((n, v) for n, v, _ in rows)["network.number"], 2)
        self.assertEqual(dict((n, v) for n, v, _ in rows)["network.text"], "x")

    def test_compare_and_summarize(self):
        rows = audit.compare(audit.parse_arkenfox(ARKENFOX), {"browser.startup.page": 3, "evergreen.same": True})
        summary = audit.summarize(rows)
        self.assertEqual([r.name for r in summary["different"]], ["browser.startup.page"])
        self.assertEqual([r.name for r in summary["same"]], ["evergreen.same"])
        self.assertEqual({r.name for r in summary["missing"]}, {"_user.js.parrot", "network.thing", "network.number", "network.text"})

    def test_firefox_defaults(self):
        defaults = audit.firefox_defaults(
            {"modules/libpref/init/StaticPrefList.yaml": STATIC, "browser/app/profile/firefox.js": FIREFOX_JS}
        )
        self.assertEqual(defaults["network.thing"], False)
        self.assertEqual(defaults["network.number"], 2)
        self.assertEqual(defaults["network.text"], "y")
        self.assertNotIn("network.build", defaults)  # depends on the build: unknown, not guessed

    def test_report_separates_gaps_from_agreement(self):
        defaults = {"network.thing": False, "network.number": 2, "network.text": "y"}
        text = audit.report(ARKENFOX, defaults=defaults)
        self.assertIn("network.thing: True / False", text)  # arkenfox differs from Firefox: a gap
        self.assertIn("network.text: 'x' / 'y'", text)
        self.assertNotIn("network.number", text)  # Firefox already does what arkenfox wants
        self.assertNotIn("parrot", text)


if __name__ == "__main__":
    unittest.main()
