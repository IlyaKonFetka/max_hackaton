import { useEffect, useState } from 'react'

/** Картинка с бэкенда: грузим через fetch с заголовком обхода страницы ngrok и показываем как blob. */
export function Photo({ src, alt = '' }: { src: string; alt?: string }) {
  const [url, setUrl] = useState<string | null>(null)
  useEffect(() => {
    let revoke: string | null = null
    let cancelled = false
    fetch(src, { headers: { 'ngrok-skip-browser-warning': '1' } })
      .then((r) => (r.ok ? r.blob() : Promise.reject(new Error(String(r.status)))))
      .then((b) => {
        if (cancelled) return
        revoke = URL.createObjectURL(b)
        setUrl(revoke)
      })
      .catch(() => setUrl(src))
    return () => {
      cancelled = true
      if (revoke) URL.revokeObjectURL(revoke)
    }
  }, [src])
  return url ? <img src={url} alt={alt} /> : <span className="badge">фото…</span>
}
