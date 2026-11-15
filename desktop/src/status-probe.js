// Poll the router. Every request has a timeout so a stalled router can't pile up
// pending fetches from the 4 s settings refresh.
export async function probeStatus({ base, key = "", fetchImpl = fetch, timeoutMs = 1500 }) {
  const get = (route, headers = {}) =>
    fetchImpl(`${base}${route}`, { headers, signal: AbortSignal.timeout(timeoutMs) });
  try {
    const healthResponse = await get("/health");
    const health = await healthResponse.json();
    const statusResponse = await get("/status", key ? { authorization: `Bearer ${key}` } : {});
    if (!statusResponse.ok) return { health, error: `status ${statusResponse.status}` };
    return { health, status: await statusResponse.json() };
  } catch (error) {
    return { error: error?.message || String(error) };
  }
}
