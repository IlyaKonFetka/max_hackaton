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

export const haptic = (type: 'success' | 'warning' | 'error' | 'select' | 'tap') => {
  const h = webApp()?.HapticFeedback
  try {
    if (type === 'select') h?.selectionChanged?.()
    else if (type === 'tap') h?.impactOccurred?.('light')
    else h?.notificationOccurred?.(type)
  } catch {
    /* вне MAX */
  }
}

export const apiUrl = (): string => {
  const fromConfig = window.__APP_CONFIG__?.apiUrl
  const fromEnv = import.meta.env.VITE_API_URL as string | undefined
  return (fromConfig || fromEnv || 'http://localhost:8000').replace(/\/$/, '')
}
