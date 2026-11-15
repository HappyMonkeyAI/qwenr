import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";

import {
  assertListenAllowed,
  isReplacementKey,
  keyPrefix,
  saveOperatorSettings,
  setScalar,
} from "../src/config-store.js";

test("loopback listen is allowed and LAN bind needs confirm", () => {
  assert.deepEqual(assertListenAllowed("localhost", 8787), { host: "127.0.0.1", port: 8787 });
  assert.throws(() => assertListenAllowed("0.0.0.0", 8787), /explicit confirm/);
  assert.deepEqual(assertListenAllowed("0.0.0.0", 8787, { allowLan: true }), { host: "0.0.0.0", port: 8787 });
});

test("blank or masked keys are not written", () => {
  assert.equal(isReplacementKey(""), false);
  assert.equal(isReplacementKey("sk-sp-H..."), false);
  assert.equal(isReplacementKey("sk-sp-real-key-value"), true);
  assert.equal(keyPrefix("sk-sp-secret"), "sk-sp-se...");
});

test("save updates listen and flags without replacing an existing key", () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "qwenr-tray-"));
  fs.writeFileSync(path.join(root, "config.example.yaml"), "listen: 127.0.0.1:8788\nfree_first: true\npayg_allow_paid: false\nfree_tier_sync: true\n");
  fs.copyFileSync(path.join(root, "config.example.yaml"), path.join(root, "config.yaml"));
  fs.writeFileSync(path.join(root, ".env"), "QWEN_TOKEN_PLAN_API_KEY=sk-sp-existing-value\n");
  const view = saveOperatorSettings(root, {
    listenHost: "127.0.0.1",
    listenPort: 8787,
    tokenPlanOnly: true,
    freeFirst: true,
    freeTierSync: false,
    paygAllowPaid: false,
    tokenPlanKey: "sk-sp-ex...",
  });
  assert.equal(view.listenPort, 8787);
  assert.equal(view.tokenPlanOnly, true);
  assert.match(fs.readFileSync(path.join(root, "config.yaml"), "utf8"), /token_plan_only: true/);
  assert.equal(view.freeTierSync, false);
  assert.equal(view.tokenPlanPrefix, "sk-sp-ex...");
  const env = fs.readFileSync(path.join(root, ".env"), "utf8");
  assert.match(env, /sk-sp-existing-value/);
  const config = fs.readFileSync(path.join(root, "config.yaml"), "utf8");
  assert.match(setScalar(config, "listen", "127.0.0.1:8787"), /listen: 127\.0\.0\.1:8787/);
});
