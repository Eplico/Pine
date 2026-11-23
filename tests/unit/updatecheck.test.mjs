/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

import assert from "node:assert/strict";
import { test } from "node:test";

import * as U from "../../src/browser/components/evergreen/UpdateCheck.sys.mjs";

function release(tag, files, extra = {}) {
  return {
    tag_name: tag,
    html_url: `https://github.com/Eplico/Pine/releases/tag/${tag}`,
    draft: false,
    prerelease: false,
    assets: files.map(name => ({ name })),
    ...extra,
  };
}

const linux = v => release(`v${v}-linux`, [`Evergreen-${v}-linux-x86_64-portable.tar.xz`, "SHA256SUMS.txt", "evergreen-install"]);
const windows = v => release(`v${v}`, [`Evergreen-${v}-win64-setup.exe`, `Evergreen-${v}-win64-portable.zip`, "SHA256SUMS.txt"]);

test("versions compare numerically, not as text", () => {
  assert.equal(U.compareVersions("0.10", "0.9"), 1);
  assert.equal(U.compareVersions("0.2", "0.2.0"), 0);
  assert.equal(U.compareVersions("0.2.1", "0.2"), 1);
  assert.equal(U.compareVersions("0.1", "0.2"), -1);
});

test("anything that is not a version number is not comparable", () => {
  for (let bad of ["dev", "", "0.2-beta", "v0.2", null, undefined, 2]) {
    assert.equal(U.parseVersion(bad), null, String(bad));
    assert.equal(U.compareVersions(bad, "0.1"), null, String(bad));
  }
});

test("Linux and Windows each see only their own releases", () => {
  let all = [windows("0.4"), linux("0.3"), windows("0.2"), linux("0.1")];
  assert.equal(U.newestRelease(all, "linux").version, "0.3");
  assert.equal(U.newestRelease(all, "win").version, "0.4");
  assert.equal(U.newestRelease(all, "macosx"), null);
});

test("the newest is chosen by version, not by list order", () => {
  assert.equal(U.newestRelease([linux("0.2"), linux("0.10"), linux("0.9")], "linux").version, "0.10");
});

test("drafts, prereleases and releases without their files are ignored", () => {
  let all = [
    release("v0.9-linux", ["Evergreen-0.9-linux-x86_64-portable.tar.xz", "SHA256SUMS.txt"], { draft: true }),
    release("v0.8-linux", ["Evergreen-0.8-linux-x86_64-portable.tar.xz", "SHA256SUMS.txt"], { prerelease: true }),
    release("v0.7-linux", ["Evergreen-0.7-linux-x86_64-portable.tar.xz"]), // no checksums
    release("v0.6-linux", ["SHA256SUMS.txt"]), // no package
    release("v0.5-linux", ["Evergreen-0.4-linux-x86_64-portable.tar.xz", "SHA256SUMS.txt"]), // wrong version in file
    linux("0.2"),
  ];
  assert.equal(U.newestRelease(all, "linux").version, "0.2");
});

test("malformed release lists do not throw", () => {
  assert.equal(U.newestRelease(null, "linux"), null);
  assert.equal(U.newestRelease([null, {}, { tag_name: 5 }], "linux"), null);
  assert.equal(U.newestRelease([{ tag_name: "v1.0-linux", assets: null }], "linux"), null);
});

test("an update is a strictly newer version", () => {
  let r = U.newestRelease([linux("0.3")], "linux");
  assert.equal(U.isUpdate("0.2", r), true);
  assert.equal(U.isUpdate("0.3", r), false);
  assert.equal(U.isUpdate("0.4", r), false);
  assert.equal(U.isUpdate("dev", r), false);
  assert.equal(U.isUpdate("0.2", null), false);
});

test("only https release pages are kept", () => {
  assert.equal(U.newestRelease([linux("0.3")], "linux").pageUrl, "https://github.com/Eplico/Pine/releases/tag/v0.3-linux");
  assert.equal(U.newestRelease([{ ...linux("0.3"), html_url: "javascript:alert(1)" }], "linux").pageUrl, "");
  assert.equal(U.newestRelease([{ ...linux("0.3"), html_url: "http://example.com/" }], "linux").pageUrl, "");
});

test("checksum files are read by name", () => {
  let a = "a".repeat(64);
  let b = "B".repeat(64);
  let sums = U.parseChecksums(`${a}  one.exe\r\n${b} *two.zip\nnot a line\n${"c".repeat(63)}  short\n`);
  assert.deepEqual(sums, { "one.exe": a, "two.zip": "b".repeat(64) });
  assert.deepEqual(U.parseChecksums(null), {});
});

test("the Windows installer's name follows the version", () => {
  assert.equal(U.windowsSetupName("0.3"), "Evergreen-0.3-win64-setup.exe");
  assert.equal(U.windowsSetupName("../0.3"), null);
});

test("downloads only come from https (or this computer) and plain release names", () => {
  let base = "https://github.com/Eplico/Pine/releases/download/";
  assert.equal(U.releaseFileUrl(base, "v0.3", "Evergreen-0.3-win64-setup.exe"), `${base}v0.3/Evergreen-0.3-win64-setup.exe`);
  assert.equal(U.releaseFileUrl(base.slice(0, -1), "v0.3-linux", "SHA256SUMS.txt"), `${base}v0.3-linux/SHA256SUMS.txt`);
  assert.equal(U.releaseFileUrl("http://127.0.0.1:8080/", "v0.3", "a.exe"), "http://127.0.0.1:8080/v0.3/a.exe");
  assert.equal(U.releaseFileUrl("http://example.com/", "v0.3", "a.exe"), null);
  assert.equal(U.releaseFileUrl("ftp://example.com/", "v0.3", "a.exe"), null);
  assert.equal(U.releaseFileUrl("not a url", "v0.3", "a.exe"), null);
  assert.equal(U.releaseFileUrl(base, "v0.3/../x", "a.exe"), null);
  assert.equal(U.releaseFileUrl(base, "v0.3", "../a.exe"), null);
  assert.equal(U.releaseFileUrl(base, "main", "a.exe"), null);
});
