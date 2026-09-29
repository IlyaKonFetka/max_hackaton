/** Обёртка над MAX Bridge (window.WebApp). В обычном браузере WebApp нет, поэтому вызовы молча ничего не делают. */

export interface MaxWebApp {
  initData?: string
  initDataUnsafe?: {
    user?: { id: number; first_name?: string; last_name?: string; username?: string }
    start_param?: string
    auth_date?: number
  }
  HapticFeedback?: {
    impactOccurred?: (style: string) => void
    notificationOccurred?: (type: 'success' | 'warning' | 'error') => void
    selectionChanged?: () => void
  }
  enableClosingConfirmation?: () => void
  disableClosingConfirmation?: () => void
}

declare global {
  interface Window {
    WebApp?: MaxWebApp
    __APP_CONFIG__?: { apiUrl?: string }
  }
}

export const webApp = (): MaxWebApp | undefined => window.WebApp

export const initData = (): string => webApp()?.initData ?? ''

export const startParam = (): string => {
  const sp = webApp()?.initDataUnsafe?.start_param
  if (sp) return sp
  // Для локальной отладки в браузере: ?start_param=s12
  return new URLSearchParams(location.search).get('start_param') ?? ''
}

export const insideMax = (): boolean => Boolean(webApp()?.initData)

/** Мост отвечает промисом; без транспорта (обычный браузер) он отклоняется — это не ошибка приложения. */
const quiet = (r: unknown) => {
  ;(r as Promise<unknown> | undefined)?.catch?.(() => {})
}

export const closingConfirmation = (on: boolean) => {
  const wa = webApp()
  try {
    quiet(on ? wa?.enableClosingConfirmation?.() : wa?.disableClosingConfirmation?.())
  } catch {
    /* вне MAX */
  }
}

export const haptic = (type: 'success' | 'warning' | 'error' | 'select' | 'tap') => {
  const h = webApp()?.HapticFeedback
  try {
    const r: unknown =
      type === 'select' ? h?.selectionChanged?.() : type === 'tap' ? h?.impactOccurred?.('light') : h?.notificationOccurred?.(type)
    quiet(r)
  } catch {
    /* вне MAX */
  }
}

export const apiUrl = (): string => {
  const fromConfig = window.__APP_CONFIG__?.apiUrl
  const fromEnv = import.meta.env.VITE_API_URL as string | undefined
  return (fromConfig || fromEnv || 'http://localhost:8000').replace(/\/$/, '')
}
