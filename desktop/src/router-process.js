import { spawn } from "node:child_process";
import fs from "node:fs";
import path from "node:path";

const STOP_TIMEOUT_MS = 5000;

export function pythonCommand(root) {
  const win = path.join(root, ".venv", "Scripts", "python.exe");
  const nix = path.join(root, ".venv", "bin", "python");
  if (fs.existsSync(win)) return win;
  if (fs.existsSync(nix)) return nix;
  return process.platform === "win32" ? "py" : "python3";
}

export function pythonArgs(command) {
  if (path.basename(command).toLowerCase() === "py" || path.basename(command).toLowerCase() === "py.exe") {
    return ["-3.12", "-m", "qwenr.server", "--config", "config.yaml"];
  }
  return ["-m", "qwenr.server", "--config", "config.yaml"];
}

export class RouterProcess {
  constructor(root, { spawnImpl = spawn, logPath = path.join(root, "router.log"), platform = process.platform } = {}) {
    this.root = root;
    this.spawnImpl = spawnImpl;
    this.logPath = logPath;
    this.platform = platform;
    this.child = null;
    this.lastError = "";
  }

  get owned() {
    return Boolean(this.child && this.child.exitCode === null && !this.child.killed);
  }

  start() {
    if (this.owned) return { started: false, reason: "already-owned" };
    const command = pythonCommand(this.root);
    // Router stdout/stderr go to a log so start failures are diagnosable.
    fs.mkdirSync(path.dirname(this.logPath), { recursive: true });
    const logFd = fs.openSync(this.logPath, "a");
    let child;
    try {
      child = this.spawnImpl(command, pythonArgs(command), {
        cwd: this.root,
        env: { ...process.env, PYTHONPATH: "src" },
        windowsHide: true,
        stdio: ["ignore", logFd, logFd],
      });
    } finally {
      fs.closeSync(logFd); // the child holds its own copy
    }
    this.child = child;
    this.lastError = "";
    child.on("exit", (code, signal) => {
      if (this.child === child) this.child = null;
      if (code && code !== 0) {
        this.lastError = `router exited ${code}${signal ? ` ${signal}` : ""} (see ${this.logPath})`;
      }
    });
    child.on("error", (error) => {
      this.lastError = error.message;
      if (this.child === child) this.child = null;
    });
    return { started: true, pid: child.pid };
  }

  // Resolves once the process has exited (or after a timeout), so a following
  // start() doesn't race the old process for the port.
  stop() {
    const child = this.child;
    this.child = null;
    if (!child || child.exitCode !== null) return Promise.resolve({ stopped: false });
    const exited = new Promise((resolve) => {
      const timer = setTimeout(() => resolve({ stopped: true, timedOut: true }), STOP_TIMEOUT_MS);
      child.once("exit", () => {
        clearTimeout(timer);
        resolve({ stopped: true });
      });
    });
    if (this.platform === "win32") {
      this.spawnImpl("taskkill", ["/PID", String(child.pid), "/T", "/F"], { windowsHide: true, stdio: "ignore" });
    } else {
      child.kill();
    }
    return exited;
  }

  async restart() {
    await this.stop();
    return this.start();
  }
}
