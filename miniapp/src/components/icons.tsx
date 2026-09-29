/** Иконки тонкой линией вместо эмодзи. Цвет берут от текста кнопки. */

const line = { fill: 'none', stroke: 'currentColor', strokeWidth: 1.8, strokeLinecap: 'round', strokeLinejoin: 'round' } as const

export function BotIcon({ size = 18 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" aria-hidden {...line}>
      <path d="M12 3.5v3" />
      <rect x="4.5" y="6.5" width="15" height="12" rx="3.5" />
      <path d="M2.5 11v3M21.5 11v3" />
      <circle cx="9.5" cy="12.5" r="1.1" fill="currentColor" stroke="none" />
      <circle cx="14.5" cy="12.5" r="1.1" fill="currentColor" stroke="none" />
    </svg>
  )
}

export function CameraIcon({ size = 18 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" aria-hidden {...line}>
      <path d="M8.5 6.5 10 4.5h4l1.5 2" />
      <rect x="3" y="6.5" width="18" height="13" rx="3" />
      <circle cx="12" cy="13" r="3.5" />
    </svg>
  )
}

export function PlaneIcon({ size = 18 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" aria-hidden>
      <path fill="currentColor" d="M3.4 20.4 21.9 12 3.4 3.6 3.4 10.1 16.6 12 3.4 13.9z" />
    </svg>
  )
}
