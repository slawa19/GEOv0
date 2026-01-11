import fs from 'node:fs/promises'
import path from 'node:path'

const repoRoot = path.resolve(process.cwd(), '..')
const sourceV1 = path.join(repoRoot, 'admin-fixtures', 'v1')
const destV1 = path.join(process.cwd(), 'public', 'admin-fixtures', 'v1')

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
  if (!(await exists(sourceV1))) {
    throw new Error(`Source fixtures not found: ${sourceV1}`)
  }

  await copyDir(sourceV1, destV1)
  console.log(`Synced fixtures: ${path.relative(process.cwd(), destV1)}`)
}

main().catch((err) => {
  console.error(err)
  process.exit(1)
})
