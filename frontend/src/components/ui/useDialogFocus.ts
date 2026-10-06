import { useEffect, useRef } from "react"
// Shared keyboard containment and focus restoration for existing dialogs.
export function useDialogFocus(open: boolean, close: () => void, locked = false) {
  const ref = useRef<HTMLDivElement>(null)
  const current = useRef({ close, locked })
  useEffect(() => { current.current = { close, locked } }, [close, locked])
  useEffect(() => {
    if (!open) return
    const previous = document.activeElement as HTMLElement | null
    const dialog = ref.current
    const targets = () => [...(dialog?.querySelectorAll<HTMLElement>('button:not(:disabled), input:not(:disabled):not([type="hidden"]), textarea:not(:disabled), select:not(:disabled), a[href], [tabindex="0"]') ?? [])].filter(e => e.getClientRects().length)
    if (!dialog?.contains(document.activeElement)) (targets().find(e => e.tagName === "INPUT" || e.tagName === "TEXTAREA") ?? targets()[0])?.focus()
    const key = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !current.current.locked) { event.preventDefault(); current.current.close() }
      if (event.key !== "Tab") return
      const items = targets(), first = items[0], last = items.at(-1)
      if (!first) { event.preventDefault(); return }
      if (event.shiftKey && (document.activeElement === first || !dialog?.contains(document.activeElement))) { event.preventDefault(); last?.focus() }
      else if (!event.shiftKey && (document.activeElement === last || !dialog?.contains(document.activeElement))) { event.preventDefault(); first.focus() }
    }
    document.addEventListener("keydown", key)
    return () => { document.removeEventListener("keydown", key); previous?.focus() }
  }, [open])
  return ref
}
