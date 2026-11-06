// Copy the pdf.js worker out of node_modules into public/ so PDF viewing works with no
// network access. pdfjs-dist loads its worker over HTTP (react-pdf defaults to a CDN
// URL), so an air-gapped deployment needs a same-origin copy. Runs on postinstall and
// before `next build`; the version always matches the installed pdfjs-dist.
import { copyFile, mkdir } from 'node:fs/promises'
import { createRequire } from 'node:module'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const here = path.dirname(fileURLToPath(import.meta.url))
const webRoot = path.resolve(here, '..')
const require = createRequire(import.meta.url)

const WORKER_FILE = 'pdf.worker.min.mjs'

try {
  const pdfjsPkg = require.resolve('pdfjs-dist/package.json', { paths: [webRoot] })
  const source = path.join(path.dirname(pdfjsPkg), 'build', WORKER_FILE)
  const destination = path.join(webRoot, 'public', WORKER_FILE)

  await mkdir(path.dirname(destination), { recursive: true })
  await copyFile(source, destination)
  console.log(`[pdfjs] copied ${WORKER_FILE} into public/`)
} catch (error) {
  // Never fail install/build over this: the app still runs, only in-document PDF
  // rendering degrades, and the error message says exactly how to fix it.
  console.warn(
    `[pdfjs] could not vendor ${WORKER_FILE} (${error.message}). ` +
      'PDF previews will need network access until this is resolved.',
  )
}
