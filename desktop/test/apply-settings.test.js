import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";

import { applySavedSettings, routingChanged, routingMismatch } from "../src/apply-settings.js";
import { loadOperatorView, readScalar, saveOperatorSettings } from "../src/config-store.js";

const BASE = {
  listenHost: "127.0.0.1",
  listenPort: 8788,
  tokenPlanOnly: false,
  freeFirst: true,
  freeTierSync: true,
  paygAllowPaid: false,
  tokenPlanPrefix: "sk-sp-ab...",
  paygPrefix: "sk-ws-cd...",
};

function fakeRouter(owned) {
  const calls = [];
  return {
    calls,
    get owned() {
      return owned;
    },
    restart: async () => {
      calls.push("restart");
      return { started: true };
    },
  };
}

// --- 1. Apply on save --------------------------------------------------------

test("routingChanged lists only settings the running router depends on", () => {
  assert.deepEqual(routingChanged(BASE, { ...BASE }), []);
  assert.deepEqual(routingChanged(BASE, { ...BASE, tokenPlanOnly: true }), ["tokenPlanOnly"]);
  assert.deepEqual(routingChanged(BASE, { ...BASE, paygPrefix: "sk-ws-zz..." }), ["paygPrefix"]);
  assert.deepEqual(routingChanged(BASE, { ...BASE, configPath: "elsewhere" }), []);
});

test("owned router restarts when a routing setting changes", async () => {
  const router = fakeRouter(true);
  const result = await applySavedSettings({ before: BASE, after: { ...BASE, tokenPlanOnly: true }, router, externalLive: false });
  assert.deepEqual(router.calls, ["restart"]);
  assert.equal(result.restarted, true);
  assert.equal(result.warning, undefined);
});

test("router started outside the tray is not touched and the save warns loudly", async () => {
  const router = fakeRouter(false);
  const result = await applySavedSettings({ before: BASE, after: { ...BASE, tokenPlanOnly: true }, router, externalLive: true });
  assert.deepEqual(router.calls, []);
  assert.equal(result.restarted, false);
  assert.match(result.warning, /outside the tray/);
  assert.match(result.warning, /old settings/);
});

test("stopped router or unchanged settings: nothing to apply", async () => {
  const stopped = fakeRouter(false);
  assert.deepEqual(
    await applySavedSettings({ before: BASE, after: { ...BASE, tokenPlanOnly: true }, router: stopped, externalLive: false }),
    { restarted: false, changed: ["tokenPlanOnly"] },
  );
  const owned = fakeRouter(true);
  await applySavedSettings({ before: BASE, after: { ...BASE }, router: owned, externalLive: false });
  assert.deepEqual(owned.calls, []);
});

// --- 2. Saved vs live mismatch -----------------------------------------------

test("mismatch warns that PAYG is still active when Token Plan only is not live", () => {
  const live = { token_plan_only: false, free_first: true, payg_allow_paid: false };
  const messages = routingMismatch({ ...BASE, tokenPlanOnly: true }, live);
  assert.equal(messages.length, 1);
  assert.match(messages[0], /PAYG still active/);
  assert.match(messages[0], /restart/i);
});

test("mismatch flags paid PAYG live but off in saved settings", () => {
  const live = { token_plan_only: false, free_first: true, payg_allow_paid: true };
  assert.match(routingMismatch(BASE, live).join("\n"), /paid PAYG/i);
});

test("no mismatch when saved matches live, or the router is down", () => {
  const live = { token_plan_only: false, free_first: true, payg_allow_paid: false };
  assert.deepEqual(routingMismatch(BASE, live), []);
  assert.deepEqual(routingMismatch(BASE, undefined), []);
});

// --- 3. Duplicate keys -------------------------------------------------------

function rootWith(configText) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "qwenr-dup-"));
  fs.writeFileSync(path.join(root, "config.yaml"), configText);
  return root;
}

const DUP = "token_plan_only: true\nlisten: 127.0.0.1:8788\ntoken_plan:\n  base_url: x\ntoken_plan_only: false\n";

test("readScalar returns the last occurrence, like the router's YAML parser", () => {
  assert.equal(readScalar(DUP, "token_plan_only"), "false");
});

test("loadOperatorView reports duplicate managed keys", () => {
  const view = loadOperatorView(rootWith(DUP));
  assert.equal(view.tokenPlanOnly, false);
  assert.deepEqual(view.duplicateKeys, ["token_plan_only"]);
});

test("save refuses when a managed key appears more than once, and writes nothing", () => {
  const root = rootWith(DUP);
  assert.throws(() => saveOperatorSettings(root, { ...BASE, tokenPlanOnly: true }), /token_plan_only.*2 times/);
  assert.equal(fs.readFileSync(path.join(root, "config.yaml"), "utf8"), DUP);
});

test("nested keys with the same name are not duplicates", () => {
  const view = loadOperatorView(rootWith("listen: 127.0.0.1:8788\npayg:\n  token_plan_only: x\ntoken_plan_only: true\n"));
  assert.deepEqual(view.duplicateKeys, []);
  assert.equal(view.tokenPlanOnly, true);
});

test("tray reads flags with the same words as the router", () => {
  const view = loadOperatorView(rootWith("listen: 127.0.0.1:8788\ntoken_plan_only: yes\nfree_first: off\n"));
  assert.equal(view.tokenPlanOnly, true);
  assert.equal(view.freeFirst, false);
});
