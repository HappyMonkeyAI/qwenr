// The router reads config.yaml and .env once at startup. These helpers make a
// saved change actually reach it, and surface when it hasn't.

// Operator-view fields the running router depends on. The key prefixes stand
// in for the .env keys themselves.
export const ROUTING_FIELDS = [
  "listenHost",
  "listenPort",
  "tokenPlanOnly",
  "freeFirst",
  "freeTierSync",
  "paygAllowPaid",
  "tokenPlanPrefix",
  "paygPrefix",
];

export function routingChanged(before, after) {
  return ROUTING_FIELDS.filter((field) => before?.[field] !== after?.[field]);
}

// Restart a tray-owned router so the change takes effect now. A router started
// elsewhere can't be restarted from here, so say so plainly: it keeps the old
// settings (including PAYG) until someone restarts it.
export async function applySavedSettings({ before, after, router, externalLive }) {
  const changed = routingChanged(before, after);
  if (changed.length === 0) return { restarted: false, changed };
  if (router.owned) {
    await router.restart();
    return { restarted: true, changed };
  }
  if (externalLive) {
    return {
      restarted: false,
      changed,
      warning:
        "Saved, but the router was started outside the tray and still uses the old settings. " +
        "Restart it manually to apply them.",
    };
  }
  return { restarted: false, changed };
}

// Compare saved settings with what the running router reports in /status.
export function routingMismatch(saved, liveStatus) {
  if (!liveStatus) return [];
  const messages = [];
  if (Boolean(saved.tokenPlanOnly) !== Boolean(liveStatus.token_plan_only)) {
    messages.push(
      saved.tokenPlanOnly
        ? "PAYG still active: Token Plan only is saved but not running. Restart the router."
        : "Router is in Token Plan only mode but saved settings are not. Restart the router.",
    );
  }
  if (Boolean(saved.paygAllowPaid) !== Boolean(liveStatus.payg_allow_paid)) {
    messages.push(
      liveStatus.payg_allow_paid
        ? "Paid PAYG is allowed in the running router but off in saved settings. Restart the router."
        : "Paid PAYG is saved as allowed but not running. Restart the router.",
    );
  }
  if (Boolean(saved.freeFirst) !== Boolean(liveStatus.free_first)) {
    messages.push("Free-first differs between saved settings and the running router. Restart the router.");
  }
  return messages;
}
