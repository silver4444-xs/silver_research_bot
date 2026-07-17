export function useApi() {
  async function api(url, opts = {}) {
    const res = await fetch(url, {
      ...opts,
      ...(opts.body instanceof FormData
        ? {}
        : { headers: { 'Content-Type': 'application/json', ...(opts.headers || {}) } }),
    })
    if (!res.ok) throw new Error(await res.text())
    return res.json()
  }
  return { api }
}
