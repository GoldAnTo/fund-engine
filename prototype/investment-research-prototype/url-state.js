export function canonicalizeParams(input = '') {
  const source = input instanceof URLSearchParams ? input : new URLSearchParams(input);
  const canonical = new URLSearchParams();
  for (const [key, value] of source) {
    if (!canonical.has(key)) canonical.set(key, value);
  }
  return canonical;
}

export function updateParams(input, updates) {
  const next = canonicalizeParams(input);
  for (const [key, value] of Object.entries(updates)) {
    if (value === null || value === undefined || value === '') next.delete(key);
    else next.set(key, String(value));
  }
  return next;
}

export function urlWithParams(input, updates) {
  return `?${updateParams(input, updates)}`;
}
