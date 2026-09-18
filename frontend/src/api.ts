export async function api<T>(path: string, body?: unknown): Promise<T> {
  const res = await fetch('/api' + path, {
    credentials: 'same-origin', method: body === undefined ? 'GET' : 'POST',
    headers: { 'Content-Type': 'application/json', 'X-Nexus-Request': '1' },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!res.ok) {
    const error = await res.json().catch(() => ({}));
    throw new Error(typeof error.detail === 'string' ? error.detail : `请求失败 (${res.status})`);
  }
  return res.json();
}
