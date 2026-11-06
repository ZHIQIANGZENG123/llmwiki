import type * as React from 'react'
import { toast } from 'sonner'
import { apiFetch } from '@/lib/api'
import type { DocumentListItem } from '@/lib/types'
import { getAccessTokenOrToast } from './helpers'

type Params = {
  kbId: string
  userId: string | undefined
  setDocuments: React.Dispatch<React.SetStateAction<DocumentListItem[]>>
  filesViewActive: boolean
  openFilesView: () => void
  /** Called after a folder is created so the grid can re-read the directory listing. */
  onFolderCreated?: () => void
}

export interface DocumentMutations {
  handleCreateNote: (targetPath?: string) => Promise<void>
  handleCreateFolder: (folderName: string, parentPath?: string) => void
  handleMoveDocument: (docId: string, targetPath: string) => Promise<void>
  handleDeleteDocument: (docId: string) => Promise<void>
  handleRenameDocument: (docId: string, newTitle: string) => Promise<void>
}

/** Single-document create, move, rename, and delete, applied to the local list on success. */
export function useDocumentMutations({ kbId, userId, setDocuments, filesViewActive, openFilesView, onFolderCreated }: Params): DocumentMutations {
  const showCreatedDocument = (data: DocumentListItem): void => {
    setDocuments((prev) => [data, ...prev])
    if (!filesViewActive) openFilesView()
  }

  const handleCreateNote = async (targetPath: string = '/'): Promise<void> => {
    const token = getAccessTokenOrToast()
    if (!token || !userId) return
    try {
      showCreatedDocument(await createNote(kbId, token, targetPath))
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Failed to create note')
    }
  }

  const handleCreateFolder = (folderName: string, parentPath: string = '/'): void => {
    const token = getAccessTokenOrToast()
    if (!token || !userId) return
    const path = parentPath.replace(/\/$/, '') + '/' + folderName + '/'
    // Ask the server to make the directory. Creating a note inside it — the previous
    // approach — left an empty `Untitled.md` in every folder that was created.
    apiFetch<{ path: string }>('/v1/folders', token, {
      method: 'POST',
      body: JSON.stringify({ path }),
    })
      .then(() => onFolderCreated?.())
      .catch((err: Error) => toast.error(err.message || 'Failed to create folder'))
  }

  const handleMoveDocument = async (docId: string, targetPath: string): Promise<void> => {
    const token = getAccessTokenOrToast()
    if (!token) return
    try {
      await patchDocument(docId, token, { path: targetPath })
      setDocuments((prev) => prev.map((d) => d.id === docId ? { ...d, path: targetPath } : d))
    } catch { toast.error('Failed to move document') }
  }

  const handleDeleteDocument = async (docId: string): Promise<void> => {
    const token = getAccessTokenOrToast()
    if (!token) return
    try {
      await apiFetch(`/v1/documents/${docId}`, token, { method: 'DELETE' })
      setDocuments((prev) => prev.filter((d) => d.id !== docId))
    } catch { toast.error('Failed to delete document') }
  }

  const handleRenameDocument = async (docId: string, newTitle: string): Promise<void> => {
    const token = getAccessTokenOrToast()
    if (!token) return
    try {
      await patchDocument(docId, token, { title: newTitle })
      setDocuments((prev) => prev.map((d) => d.id === docId ? { ...d, title: newTitle } : d))
    } catch { toast.error('Failed to rename document') }
  }

  return { handleCreateNote, handleCreateFolder, handleMoveDocument, handleDeleteDocument, handleRenameDocument }
}

function createNote(kbId: string, token: string, path: string): Promise<DocumentListItem> {
  return apiFetch<DocumentListItem>(`/v1/knowledge-bases/${kbId}/documents/note`, token, {
    method: 'POST',
    body: JSON.stringify({ filename: 'Untitled.md', path }),
  })
}

function patchDocument(docId: string, token: string, changes: { path?: string; title?: string }): Promise<unknown> {
  return apiFetch(`/v1/documents/${docId}`, token, { method: 'PATCH', body: JSON.stringify(changes) })
}
