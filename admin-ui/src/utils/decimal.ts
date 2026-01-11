type BigDec = { i: bigint; scale: number }

function parseDecimal(s: string): BigDec | null {
  const v = s.trim()
  if (!v) return null
  const m = /^-?\d+(?:\.\d+)?$/.exec(v)
  if (!m) return null
  const neg = v.startsWith('-')
  const parts = (neg ? v.slice(1) : v).split('.')
  const intPart = parts[0] ?? '0'
  const fracPart = parts[1] ?? ''
  const scale = fracPart.length
  const digits = (intPart + fracPart).replace(/^0+(?=\d)/, '') || '0'
  let i = BigInt(digits)
  if (neg) i = -i
  return { i, scale }
}

function pow10(n: number): bigint {
  let out = 1n
  for (let i = 0; i < n; i++) out *= 10n
  return out
}

function align(a: BigDec, b: BigDec): [bigint, bigint, number] {
  const s = Math.max(a.scale, b.scale)
  const ai = a.i * pow10(s - a.scale)
  const bi = b.i * pow10(s - b.scale)
  return [ai, bi, s]
}

export function isRatioBelowThreshold(opts: {
  numerator: string
  denominator: string
  threshold: string
}): boolean {
  const num = parseDecimal(opts.numerator)
  const den = parseDecimal(opts.denominator)
  const thr = parseDecimal(opts.threshold)
  if (!num || !den || !thr) return false
  if (den.i === 0n) return false
  if (thr.i < 0n) return false

  // Compare: num/den < thr  => num < den * thr
  // Use integer math: num_i * 10^(S) < den_i * thr_i * 10^(S - thr_scale)
  // We align num and den first, then multiply by threshold.
  const [numI, denI] = align(num, den)

  // den * thr
  const right = denI * thr.i
  const rightScale = thr.scale

  // left is num with scale 0 (already aligned against den)
  // Convert comparison to same scale by multiplying left by 10^rightScale.
  const left = numI * pow10(rightScale)
  return left < right
}
