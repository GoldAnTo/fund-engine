const storageName = "fundclaw:create-submission";
/** Persist only digest and key; never put pasted source material in browser storage. */
export async function submissionKey(payload: object): Promise<string> {
  if (!crypto.subtle) throw new Error("安全提交需要 HTTPS 或本机连接，请检查访问地址后重试。");
  const bytes = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(JSON.stringify(payload)));
  const hash = Array.from(new Uint8Array(bytes), (value) => value.toString(16).padStart(2, "0")).join("");
  let saved: { hash?: string; key?: string } | null = null;
  try { saved = JSON.parse(sessionStorage.getItem(storageName) ?? "null") as { hash?: string; key?: string } | null; } catch { /* Invalid records are replaced below. */ }
  if (saved?.hash === hash && typeof saved.key === "string") return saved.key;
  const key = crypto.randomUUID();
  try { sessionStorage.setItem(storageName, JSON.stringify({ hash, key })); }
  catch { throw new Error("无法保存本次提交标识，请启用当前站点的会话存储后重试。"); }
  return key;
}
export function clearSubmissionKey(key: string): void {
  try {
    const saved = JSON.parse(sessionStorage.getItem(storageName) ?? "null") as { key?: string } | null;
    if (saved?.key === key) sessionStorage.removeItem(storageName);
  } catch { /* A completed request stays completed even if storage was cleared. */ }
}
