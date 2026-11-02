// Thin fetch wrapper for the local Blendora server.
async function req(method, url, body) {
  const opts = { method, headers: {} };
  if (body instanceof FormData) opts.body = body;
  else if (body !== undefined) { opts.body = JSON.stringify(body); opts.headers["content-type"] = "application/json"; }
  const r = await fetch(url, opts);
  if (!r.ok) {
    let msg = r.statusText;
    try { const j = await r.json(); msg = j.detail || msg; if (typeof msg !== "string") msg = JSON.stringify(msg); } catch {}
    throw new Error(msg);
  }
  const ct = r.headers.get("content-type") || "";
  return ct.includes("json") ? r.json() : r.text();
}

export const api = {
  get: (u) => req("GET", u),
  post: (u, b = {}) => req("POST", u, b),
  put: (u, b) => req("PUT", u, b),
  patch: (u, b) => req("PATCH", u, b),
  del: (u) => req("DELETE", u),
  // poll a background job until it finishes
  async waitJob(job, onProgress) {
    while (job.status === "running") {
      onProgress?.(job.progress || 0);
      await new Promise((r) => setTimeout(r, 1000));
      job = await req("GET", `/api/jobs/${job.id}`);
    }
    if (job.status === "error") throw new Error(job.error || "Zadanie nie powiodło się");
    return job;
  },
};
