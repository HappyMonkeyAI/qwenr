import { spawn } from "node:child_process";
import fs from "node:fs";
import path from "node:path";

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
  constructor(root) {
    this.root = root;
    this.child = null;
    this.lastError = "";
  }

  get owned() {
    return Boolean(this.child && this.child.exitCode === null && !this.child.killed);
  }

  start() {
    if (this.owned) return { started: false, reason: "already-owned" };
    const command = pythonCommand(this.root);
    const child = spawn(command, pythonArgs(command), {
      cwd: this.root,
      env: { ...process.env, PYTHONPATH: "src" },
      windowsHide: true,
      stdio: "ignore",
    });
    this.child = child;
    this.lastError = "";
    child.on("exit", (code, signal) => {
      if (this.child === child) this.child = null;
      if (code && code !== 0) this.lastError = `router exited ${code}${signal ? ` ${signal}` : ""}`;
    });
    child.on("error", (error) => {
      this.lastError = error.message;
      if (this.child === child) this.child = null;
    });
    return { started: true, pid: child.pid };
  }

  stop() {
    const child = this.child;
    if (!child || child.exitCode !== null) {
      this.child = null;
      return { stopped: false };
    }
    if (process.platform === "win32") {
      spawn("taskkill", ["/PID", String(child.pid), "/T", "/F"], { windowsHide: true, stdio: "ignore" });
    } else {
      child.kill();
    }
    this.child = null;
    return { stopped: true };
  }
}
