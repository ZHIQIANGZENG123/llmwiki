import { pdfjs } from 'react-pdf'

// Served from public/, vendored by scripts/copy-pdf-worker.mjs during install and build,
// so PDF rendering works with no network access (react-pdf defaults to a CDN URL).
pdfjs.GlobalWorkerOptions.workerSrc = '/pdf.worker.min.mjs'

export { pdfjs }
