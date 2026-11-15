import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";

import { ensureConfig, loadOperatorView, saveOperatorSettings, setScalar, upsertEnv } from "../src/config-store.js";
import { guardIpc } from "../src/ipc-guard.js";
import { RouterProcess } from "../src/router-process.js";
import { probeStatus } from "../src/status-probe.js";

function tempRoot() {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "qwenr-tray-"));
  fs.writeFileSync(path.join(root, "config.example.yaml"), "listen: 127.0.0.1:8788\nfree_first: true\n");
  return root;
}

// --- config-store ----------------------------------------------------------

test("replacement strings are literal, not $-patterns", () => {
  assert.equal(upsertEnv("K=old\n", "K", "a$&b$'c"), "K=a$&b$'c\n");
  assert.equal(setScalar("listen: x\n", "listen", "$&"), "listen: $&\n");
});

test("keys containing newlines are rejected, not written", () => {
  const root = tempRoot();
  ensureConfig(root);
  const before = fs.existsSync(path.join(root, ".env")) ? fs.readFileSync(path.join(root, ".env"), "utf8") : "";
  assert.throws(
    () => saveOperatorSettings(root, { tokenPlanKey: "sk-sp-real-value\nQWENR_API_KEY=" }),
    /single line/,
  );
  const after = fs.existsSync(path.join(root, ".env")) ? fs.readFileSync(path.join(root, ".env"), "utf8") : "";
  assert.equal(after, before);
});

test("loadOperatorView is read-only; ensureConfig creates config.yaml", () => {
  const root = tempRoot();
  loadOperatorView(root);
  assert.equal(fs.existsSync(path.join(root, "config.yaml")), false);
  ensureConfig(root);
  assert.equal(fs.readFileSync(path.join(root, "config.yaml"), "utf8"), "listen: 127.0.0.1:8788\nfree_first: true\n");
});

// --- status probe ------------------------------------------------------------

test("probeStatus times out a hung router instead of hanging", async () => {
  const hung = (_url, { signal }) =>
    new Promise((_resolve, reject) => signal.addEventListener("abort", () => reject(signal.reason)));
  const started = Date.now();
  const result = await probeStatus({ base: "http://x", fetchImpl: hung, timeoutMs: 50 });
  assert.ok(result.error);
  assert.ok(Date.now() - started < 1000);
});

test("probeStatus sends the bearer key to /status only", async () => {
  const seen = [];
  const fetchImpl = async (url, init) => {
    seen.push([url, init.headers?.authorization]);
    return { ok: true, status: 200, json: async () => ({ status: "ok" }) };
  };
  const result = await probeStatus({ base: "http://x", key: "k1", fetchImpl, timeoutMs: 500 });
  assert.deepEqual(seen, [["http://x/health", undefined], ["http://x/status", "Bearer k1"]]);
  assert.ok(result.health && result.status);
});

// --- IPC guard -------------------------------------------------------------

test("guardIpc rejects senders other than the settings window", async () => {
  const win = { webContents: {}, isDestroyed: () => false };
  const handler = guardIpc(() => win, async (_event, x) => x * 2);
  assert.equal(await handler({ sender: win.webContents }, 21), 42);
  await assert.rejects(() => handler({ sender: {} }, 1), /untrusted renderer/);
  const noWindow = guardIpc(() => null, async () => 1);
  await assert.rejects(() => noWindow({ sender: {} }), /untrusted renderer/);
});

// --- router process ----------------------------------------------------------

function fakeSpawn() {
  const calls = [];
  const spawnImpl = (command, args, options) => {
    const child = new EventEmitter();
    child.pid = 1000 + calls.length;
    child.exitCode = null;
    child.killed = false;
    child.kill = () => {
      child.killed = true;
      setTimeout(() => {
        child.exitCode = 0;
        child.emit("exit", 0, null);
      }, 20);
    };
    calls.push({ command, args, options, child });
    return child;
  };
  return { spawnImpl, calls };
}

test("router output goes to a log file, not /dev/null", () => {
  const root = tempRoot();
  const logPath = path.join(root, "logs", "router.log");
  const { spawnImpl, calls } = fakeSpawn();
  const router = new RouterProcess(root, { spawnImpl, logPath, platform: "linux" });
  router.start();
  const stdio = calls[0].options.stdio;
  assert.equal(stdio[0], "ignore");
  assert.equal(typeof stdio[1], "number");
  assert.equal(stdio[1], stdio[2]);
  assert.ok(fs.existsSync(logPath));
});

test("stop resolves only after the child exits; restart waits for it", async () => {
  const root = tempRoot();
  const { spawnImpl, calls } = fakeSpawn();
  const router = new RouterProcess(root, { spawnImpl, logPath: path.join(root, "router.log"), platform: "linux" });
  router.start();
  const first = calls[0].child;
  const stopped = router.stop();
  assert.equal(first.exitCode, null, "not exited yet");
  await stopped;
  assert.equal(first.exitCode, 0);

  router.start();
  const second = calls[1].child;
  await router.restart();
  assert.equal(second.exitCode, 0, "restart must wait for the old process to exit");
  assert.equal(calls.length, 3);
  assert.equal(router.owned, true);
});

// --- Follow-up: keys are matched literally, not as regex -------------------

test("setScalar/readScalar/upsertEnv treat the key literally", async () => {
  const { readScalar } = await import("../src/config-store.js");
  assert.equal(setScalar("aXb: 1\n", "a.b", "2"), "a.b: 2\naXb: 1\n");
  assert.equal(readScalar("aXb: 1\n", "a.b"), "");
  assert.equal(upsertEnv("AXB=1\n", "A.B", "2"), "AXB=1\nA.B=2\n");
  assert.equal(upsertEnv("K+=1\n", "K+", "2"), "K+=2\n");
});
