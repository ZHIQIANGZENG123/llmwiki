/**
 * Copy text to the clipboard.
 *
 * `navigator.clipboard` is only available in a secure context, so a LAN deployment on
 * `http://192.168.x.x` has no async clipboard at all. The legacy `execCommand` path still
 * works there, so fall back to it rather than leaving the button dead.
 */
export async function copyText(text: string): Promise<boolean> {
  if (typeof navigator !== 'undefined' && navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(text)
      return true
    } catch {
      // Permission or security error: try the legacy path below.
    }
  }
  if (typeof document === 'undefined') return false
  try {
    const area = document.createElement('textarea')
    area.value = text
    area.setAttribute('readonly', '')
    area.style.position = 'fixed'
    area.style.top = '-9999px'
    document.body.appendChild(area)
    area.select()
    const ok = document.execCommand('copy')
    area.remove()
    return ok
  } catch {
    return false
  }
}
