import fs from 'node:fs/promises'
import path from 'node:path'

const repoRoot = path.resolve(process.cwd(), '..')
const defaultSourceV1 = path.join(repoRoot, 'admin-fixtures', 'v1')
const destV1 = path.join(process.cwd(), 'public', 'admin-fixtures', 'v1')

function parseArgs(argv) {
  const out = {
    v1Dir: null,
  }

  for (let i = 0; i < argv.length; i += 1) {
    const a = argv[i]
    if (a === '--v1-dir') {
      const v = argv[i + 1]
      if (!v) throw new Error('Missing value for --v1-dir')
      out.v1Dir = path.resolve(process.cwd(), v)
      i += 1
    } else if (typeof a === 'string' && a.length > 0 && !a.startsWith('-')) {
      // Convenience / robustness: allow passing a v1 dir as positional arg.
      if (out.v1Dir) throw new Error('Multiple positional v1-dir args are not supported')
      out.v1Dir = path.resolve(process.cwd(), a)
    } else {
      throw new Error(`Unknown arg: ${a}`)
    }
  }

  return out
}

async function exists(p) {
  try {
    await fs.access(p)
    return true
  } catch {
    return false
  }
}

async function copyDir(src, dst) {
  await fs.mkdir(dst, { recursive: true })
  // Node 22 supports fs.cp
  await fs.cp(src, dst, { recursive: true, force: true })
}

async function main() {
  const opts = parseArgs(process.argv.slice(2))
  const sourceV1 = opts.v1Dir ?? defaultSourceV1

  if (!(await exists(sourceV1))) {
    throw new Error(`Source fixtures not found: ${sourceV1}`)
  }

  await copyDir(sourceV1, destV1)
  console.log(`Synced fixtures: ${path.relative(process.cwd(), destV1)}`)
  if (opts.v1Dir) {
    console.log(`- source: ${sourceV1}`)
  }
}

main().catch((err) => {
  console.error(err)
  process.exit(1)
})
