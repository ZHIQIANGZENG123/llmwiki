/**
 * Dynamic route segments arrive percent-encoded from the URL, but knowledge-base slugs
 * are stored as the raw workspace name. A workspace named `新建文件夹` (or `My Wiki`)
 * therefore never matched, and the page reported "Wiki not found".
 *
 * Decode once when the value still looks encoded; names that legitimately contain a `%`
 * are stored encoded as `%25`, so this stays lossless and is a no-op when already decoded.
 */
export function decodeSlug(value: string | null | undefined): string {
  if (!value) return ''
  if (!value.includes('%')) return value
  try {
    return decodeURIComponent(value)
  } catch {
    return value
  }
}
