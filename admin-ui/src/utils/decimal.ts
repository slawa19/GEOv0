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

function formatBigIntFixed(i: bigint, scale: number): string {
  const neg = i < 0n
  const abs = neg ? -i : i
  const s = abs.toString()
  if (scale <= 0) return (neg ? '-' : '') + s

  const pad = scale + 1
  const padded = s.length >= pad ? s : '0'.repeat(pad - s.length) + s
  const head = padded.slice(0, padded.length - scale)
  const frac = padded.slice(padded.length - scale)
  return (neg ? '-' : '') + head + '.' + frac
}

// Formats a decimal string to exactly `digits` fraction digits (default 2).
// Uses integer math and rounds half-up.
export function formatDecimalFixed(input: string, digits = 2): string {
  const dec = parseDecimal(String(input ?? ''))
  if (!dec) return String(input ?? '')

  const target = Math.max(0, Math.floor(digits))
  if (dec.scale === target) return formatBigIntFixed(dec.i, target)

  if (dec.scale < target) {
    const mul = pow10(target - dec.scale)
    return formatBigIntFixed(dec.i * mul, target)
  }

  // dec.scale > target: round half-up
  const diff = dec.scale - target
  const div = pow10(diff)
  const neg = dec.i < 0n
  const abs = neg ? -dec.i : dec.i
  const q = abs / div
  const r = abs % div
  const half = div / 2n
  const rounded = r >= half ? q + 1n : q
  const signed = neg ? -rounded : rounded
  return formatBigIntFixed(signed, target)
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
