import fs from 'node:fs/promises'
import path from 'node:path'

const distPath = path.join(process.cwd(), 'dist')

async function main() {
  await fs.rm(distPath, { recursive: true, force: true })
  console.log('Cleaned dist')
}

main().catch((err) => {
  console.error(err)
  process.exit(1)
})
