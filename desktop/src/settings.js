const host = document.querySelector("#host");
const port = document.querySelector("#port");
const tokenPlanOnly = document.querySelector("#tokenPlanOnly");
const freeFirst = document.querySelector("#freeFirst");
const freeSync = document.querySelector("#freeSync");
const paid = document.querySelector("#paid");
const login = document.querySelector("#login");
const tokenKey = document.querySelector("#tokenKey");
const paygKey = document.querySelector("#paygKey");
const tokenPrefix = document.querySelector("#tokenPrefix");
const paygPrefix = document.querySelector("#paygPrefix");
const status = document.querySelector("#status");

function fill(state) {
  const settings = state.settings;
  host.value = settings.listenHost;
  port.value = String(settings.listenPort);
  tokenPlanOnly.checked = settings.tokenPlanOnly;
  freeFirst.checked = settings.freeFirst;
  freeFirst.disabled = settings.tokenPlanOnly;
  freeSync.checked = settings.freeTierSync;
  paid.checked = settings.paygAllowPaid;
  login.checked = state.openAtLogin;
  tokenPrefix.textContent = settings.tokenPlanPrefix ? `(${settings.tokenPlanPrefix})` : "(not set)";
  paygPrefix.textContent = settings.paygPrefix ? `(${settings.paygPrefix})` : "(not set)";
  const live = state.live;
  const lines = [
    `router: ${state.router}`,
    live?.health ? `health: ${live.health.status || "ok"}` : `health: ${live?.error || "down"}`,
  ];
  if (live?.status) {
    const summary = live.status.summary || {};
    if (live.status.token_plan_only) lines.push("mode: token plan only");
    const sync = live.status.free_tier_sync || {};
    lines.push(`free open: ${(live.status.free_available || []).join(", ") || "(none)"}`);
    if (sync.closed) lines.push(`free lane closed: ${sync.closed}`);
    lines.push(`blocked: ${Object.keys(sync.blocked || {}).length}`);
    lines.push(`calls: free ${summary.free_ok || 0} · token plan ${summary.token_plan_ok || 0}`);
    const recent = (live.status.recent || [])[0];
    if (recent) lines.push(`last: ${recent.requested} → ${recent.model} (${recent.lane})`);
  }
  if (state.lastError) lines.push(state.lastError);
  status.textContent = lines.join("\n");
}

async function refresh() {
  try {
    if (!window.qwenrTray) {
      status.textContent = "Tray bridge missing.";
      return;
    }
    fill(await window.qwenrTray.getState());
  } catch (error) {
    status.textContent = error?.message || String(error);
  }
}

document.querySelector("#save").addEventListener("click", async () => {
  const result = await window.qwenrTray.save({
    listenHost: host.value.trim(),
    listenPort: Number(port.value),
    tokenPlanOnly: tokenPlanOnly.checked,
    freeFirst: freeFirst.checked,
    freeTierSync: freeSync.checked,
    paygAllowPaid: paid.checked,
    tokenPlanKey: tokenKey.value,
    paygKey: paygKey.value,
  });
  tokenKey.value = "";
  paygKey.value = "";
  if (result?.error) status.textContent = result.error;
  else await refresh();
});

document.querySelector("#start").addEventListener("click", async () => { await window.qwenrTray.start(); await refresh(); });
document.querySelector("#stop").addEventListener("click", async () => { await window.qwenrTray.stop(); await refresh(); });
document.querySelector("#restart").addEventListener("click", async () => { await window.qwenrTray.restart(); await refresh(); });
tokenPlanOnly.addEventListener("change", () => { freeFirst.disabled = tokenPlanOnly.checked; });
login.addEventListener("change", async () => { await window.qwenrTray.setLogin(login.checked); });

refresh();
setInterval(refresh, 4000);
