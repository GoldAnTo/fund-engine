// Shared helper for SourceSpan locators so multiple components render
// the same human-readable form.

export function locatorText(locator: Record<string, unknown> | null): string {
  if (!locator) return "未知位置";
  const parts: string[] = [];
  if (locator.page != null) parts.push(`第 ${locator.page} 页`);
  if (locator.paragraph != null) parts.push(`第 ${locator.paragraph} 段`);
  if (locator.table != null) parts.push(`表 ${locator.table}`);
  if (locator.row != null) parts.push(`第 ${locator.row} 行`);
  return parts.length ? parts.join("，") : JSON.stringify(locator);
}