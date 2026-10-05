import { useCallback, useEffect, useState } from "react"
export function useApiResource<T>(key: string, load: (signal: AbortSignal) => Promise<T>) {
  const [version, setVersion] = useState(0)
  const [result, setResult] = useState<{ key: string; version: number; data?: T; error?: string }>()
  useEffect(() => {
    const controller = new AbortController()
    Promise.resolve().then(() => load(controller.signal)).then(
      (data) => { if (!controller.signal.aborted) setResult({ key, version, data }) },
      (error: unknown) => { if (!controller.signal.aborted) setResult({ key, version, error: error instanceof Error ? error.message : "Unable to load backend data." }) },
    )
    return () => controller.abort()
  }, [key, version, load])
  const current = result?.key === key && result.version === version ? result : undefined
  const retry = useCallback(() => setVersion((value) => value + 1), [])
  return { data: current?.data, error: current?.error, loading: !current, retry }
}
