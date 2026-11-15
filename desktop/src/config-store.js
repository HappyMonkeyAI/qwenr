import fs from "node:fs";
import path from "node:path";

const LOOPBACK = new Set(["127.0.0.1", "localhost"]);

export function repoRootFromDesktop(desktopDir) {
  return path.resolve(desktopDir, "..");
}

export function parseListen(value) {
  const text = String(value || "").trim();
  const match = /^(.*):(\d+)$/.exec(text);
  if (!match) throw new Error(`listen must look like 127.0.0.1:8787, got ${text}`);
  const host = match[1] || "127.0.0.1";
  const port = Number(match[2]);
  if (!Number.isInteger(port) || port < 1 || port > 65535) {
    throw new Error(`invalid port in ${text}`);
  }
  return { host, port };
}

export function assertListenAllowed(host, port, { allowLan = false } = {}) {
  const parsedHost = String(host || "").trim();
  const parsedPort = Number(port);
  if (!Number.isInteger(parsedPort) || parsedPort < 1 || parsedPort > 65535) {
    throw new Error("port must be 1-65535");
  }
  if (!LOOPBACK.has(parsedHost) && parsedHost !== "0.0.0.0") {
    throw new Error("host must be 127.0.0.1, localhost, or 0.0.0.0");
  }
  if (parsedHost === "0.0.0.0" && !allowLan) {
    throw new Error("LAN bind requires an explicit confirm");
  }
  return { host: parsedHost === "localhost" ? "127.0.0.1" : parsedHost, port: parsedPort };
}

export function readScalar(text, key) {
  const match = new RegExp(`^${key}:\\s*(.*)$`, "m").exec(text);
  if (!match) return "";
  return match[1].trim().replace(/^["']|["']$/g, "");
}

export function setScalar(text, key, value) {
  const line = `${key}: ${value}`;
  const pattern = new RegExp(`^${key}:.*$`, "m");
  if (pattern.test(text)) return text.replace(pattern, line);
  return `${line}\n${text}`;
}

export function parseEnv(text) {
  const out = {};
  for (const raw of String(text || "").split(/\r?\n/)) {
    const line = raw.trim();
    if (!line || line.startsWith("#") || !line.includes("=")) continue;
    const index = line.indexOf("=");
    out[line.slice(0, index).trim()] = line.slice(index + 1).trim();
  }
  return out;
}

export function upsertEnv(text, key, value) {
  const line = `${key}=${value}`;
  const pattern = new RegExp(`^${key}=.*$`, "m");
  if (pattern.test(text)) return text.replace(pattern, line);
  const base = String(text || "");
  const sep = base.endsWith("\n") || base.length === 0 ? "" : "\n";
  return `${base}${sep}${line}\n`;
}

export function keyPrefix(value) {
  const text = String(value || "");
  if (!text) return "";
  return `${text.slice(0, 8)}...`;
}

export function isReplacementKey(value) {
  const text = String(value || "").trim();
  if (!text) return false;
  if (text.includes("...") || text.includes("•")) return false;
  return text.length >= 12;
}

export function loadOperatorView(root) {
  const configPath = path.join(root, "config.yaml");
  const examplePath = path.join(root, "config.example.yaml");
  const envPath = path.join(root, ".env");
  if (!fs.existsSync(configPath) && fs.existsSync(examplePath)) {
    fs.copyFileSync(examplePath, configPath);
  }
  const configText = fs.existsSync(configPath) ? fs.readFileSync(configPath, "utf8") : "";
  const envText = fs.existsSync(envPath) ? fs.readFileSync(envPath, "utf8") : "";
  const env = parseEnv(envText);
  const listen = parseListen(readScalar(configText, "listen") || "127.0.0.1:8787");
  return {
    listenHost: listen.host,
    listenPort: listen.port,
    tokenPlanOnly: readScalar(configText, "token_plan_only") === "true",
    freeFirst: readScalar(configText, "free_first") !== "false",
    paygAllowPaid: readScalar(configText, "payg_allow_paid") === "true",
    freeTierSync: readScalar(configText, "free_tier_sync") !== "false",
    tokenPlanPrefix: keyPrefix(env.QWEN_TOKEN_PLAN_API_KEY),
    paygPrefix: keyPrefix(env.QWEN_PAYG_API_KEY),
    configPath,
    envPath,
  };
}

export function saveOperatorSettings(root, patch) {
  const view = loadOperatorView(root);
  const listen = assertListenAllowed(patch.listenHost ?? view.listenHost, patch.listenPort ?? view.listenPort, {
    allowLan: Boolean(patch.allowLan),
  });
  const configPath = path.join(root, "config.yaml");
  let configText = fs.readFileSync(configPath, "utf8");
  configText = setScalar(configText, "listen", `${listen.host}:${listen.port}`);
  configText = setScalar(configText, "token_plan_only", patch.tokenPlanOnly ? "true" : "false");
  configText = setScalar(configText, "free_first", patch.freeFirst ? "true" : "false");
  configText = setScalar(configText, "payg_allow_paid", patch.paygAllowPaid ? "true" : "false");
  configText = setScalar(configText, "free_tier_sync", patch.freeTierSync ? "true" : "false");
  fs.writeFileSync(configPath, configText.endsWith("\n") ? configText : `${configText}\n`, "utf8");

  const envPath = path.join(root, ".env");
  let envText = fs.existsSync(envPath) ? fs.readFileSync(envPath, "utf8") : "";
  if (isReplacementKey(patch.tokenPlanKey)) {
    envText = upsertEnv(envText, "QWEN_TOKEN_PLAN_API_KEY", patch.tokenPlanKey.trim());
  }
  if (isReplacementKey(patch.paygKey)) {
    envText = upsertEnv(envText, "QWEN_PAYG_API_KEY", patch.paygKey.trim());
  }
  if (envText) fs.writeFileSync(envPath, envText.endsWith("\n") ? envText : `${envText}\n`, "utf8");
  return loadOperatorView(root);
}
