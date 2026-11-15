import fs from "node:fs";
import path from "node:path";

const LOOPBACK = new Set(["127.0.0.1", "localhost"]);

// Keys are matched literally; "a.b" must not match "aXb".
export function escapeRegExp(text) {
  return String(text).replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

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

// Top-level keys the tray writes. Each must appear at most once.
export const MANAGED_KEYS = ["listen", "token_plan_only", "free_first", "payg_allow_paid", "free_tier_sync"];

// Last occurrence wins, matching the router's YAML parser.
export function readScalar(text, key) {
  const matches = [...String(text).matchAll(new RegExp(`^${escapeRegExp(key)}:[ \\t]*(.*)$`, "gm"))];
  if (matches.length === 0) return "";
  return matches[matches.length - 1][1].trim().replace(/^["']|["']$/g, "");
}

// Managed keys appearing more than once at top level (column 0), as [key, count].
export function duplicateKeyCounts(text) {
  return MANAGED_KEYS.map((key) => [key, String(text).match(new RegExp(`^${escapeRegExp(key)}:`, "gm"))?.length ?? 0]).filter(
    ([, count]) => count > 1,
  );
}

// Same words the router accepts (config._bool). Blank or unknown -> default.
export function parseFlag(value, fallback) {
  const text = String(value || "").trim().toLowerCase();
  if (["true", "1", "yes", "on"].includes(text)) return true;
  if (["false", "0", "no", "off"].includes(text)) return false;
  return fallback;
}

export function setScalar(text, key, value) {
  const line = `${key}: ${value}`;
  const pattern = new RegExp(`^${escapeRegExp(key)}:.*$`, "m");
  // Function replacement: a string would interpret $&, $' and $` in the value.
  if (pattern.test(text)) return text.replace(pattern, () => line);
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
  const pattern = new RegExp(`^${escapeRegExp(key)}=.*$`, "m");
  if (pattern.test(text)) return text.replace(pattern, () => line);
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

// Main-process only: never include this in loadOperatorView (it reaches the renderer).
export function readRouterApiKey(root) {
  const envPath = path.join(root, ".env");
  const fromFile = fs.existsSync(envPath) ? parseEnv(fs.readFileSync(envPath, "utf8")).QWENR_API_KEY : "";
  return String(process.env.QWENR_API_KEY || fromFile || "").replace(/^["']|["']$/g, "").trim();
}

// Call once at startup. loadOperatorView runs on every refresh and must not write.
export function ensureConfig(root) {
  const configPath = path.join(root, "config.yaml");
  const examplePath = path.join(root, "config.example.yaml");
  if (!fs.existsSync(configPath) && fs.existsSync(examplePath)) {
    fs.copyFileSync(examplePath, configPath);
  }
  return configPath;
}

export function loadOperatorView(root) {
  const configPath = path.join(root, "config.yaml");
  const envPath = path.join(root, ".env");
  const configText = fs.existsSync(configPath) ? fs.readFileSync(configPath, "utf8") : "";
  const envText = fs.existsSync(envPath) ? fs.readFileSync(envPath, "utf8") : "";
  const env = parseEnv(envText);
  const listen = parseListen(readScalar(configText, "listen") || "127.0.0.1:8787");
  return {
    listenHost: listen.host,
    listenPort: listen.port,
    tokenPlanOnly: parseFlag(readScalar(configText, "token_plan_only"), false),
    freeFirst: parseFlag(readScalar(configText, "free_first"), true),
    paygAllowPaid: parseFlag(readScalar(configText, "payg_allow_paid"), false),
    freeTierSync: parseFlag(readScalar(configText, "free_tier_sync"), true),
    tokenPlanPrefix: keyPrefix(env.QWEN_TOKEN_PLAN_API_KEY),
    paygPrefix: keyPrefix(env.QWEN_PAYG_API_KEY),
    duplicateKeys: duplicateKeyCounts(configText).map(([key]) => key),
    configPath,
    envPath,
  };
}

export function saveOperatorSettings(root, patch) {
  // Validate everything before writing anything. A newline in a key would
  // inject extra lines into .env.
  for (const field of ["tokenPlanKey", "paygKey"]) {
    if (/[\r\n]/.test(String(patch[field] || ""))) throw new Error("API keys must be a single line");
  }
  // With a duplicated key the router uses the last line; rewriting only one of
  // them could show "Token Plan only: on" while PAYG stays active. Refuse.
  const existing = fs.existsSync(path.join(root, "config.yaml")) ? fs.readFileSync(path.join(root, "config.yaml"), "utf8") : "";
  const duplicates = duplicateKeyCounts(existing);
  if (duplicates.length > 0) {
    const detail = duplicates.map(([key, count]) => `${key} appears ${count} times`).join(", ");
    throw new Error(`config.yaml is ambiguous (${detail}). Remove the extra lines, then save again.`);
  }
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
