export function buildReloadUrlPreservingWmOptOut(
  currentHref: string,
  mut: (sp: URLSearchParams) => void
): string {
  const url = new URL(currentHref)

  // Legacy доступен только явным opt-out: `?wm=0`.
  // Защита от "случайного" legacy: если пользователь явно зашёл в `wm=0`,
  // любые навигационные переходы через перезагрузку должны сохранять `wm=0`.
  const explicitWm = url.searchParams.get('wm')

  mut(url.searchParams)

  if (explicitWm === '0') {
    url.searchParams.set('wm', '0')
  }

  return url.toString()
}
