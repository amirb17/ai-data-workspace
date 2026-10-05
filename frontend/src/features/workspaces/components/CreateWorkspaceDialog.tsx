import { X } from "lucide-react"
import { useRef, useState } from "react"

import { Button } from "../../../components/ui/Button"
import { Input } from "../../../components/ui/Input"

type CreateWorkspaceDialogProps = {
  open: boolean
  onClose: () => void
  onCreate: (workspace: {
    name: string
    description: string
  }) => Promise<void>
}

export function CreateWorkspaceDialog({
  open,
  onClose,
  onCreate,
}: CreateWorkspaceDialogProps) {
  const [name, setName] = useState("")
  const [description, setDescription] = useState("")
  const savingRef = useRef(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState("")

  if (!open) {
    return null
  }

  async function handleSubmit(
    event: React.FormEvent<HTMLFormElement>,
  ) {
    event.preventDefault()
    if (savingRef.current) return

    const trimmedName = name.trim()
    const trimmedDescription = description.trim()

    if (!trimmedName) {
      setError("Workspace name is required.")
      return
    }

    if (trimmedName.length < 3) {
      setError(
        "Workspace name must be at least 3 characters.",
      )
      return
    }

    savingRef.current = true
    setSaving(true)
    try {
      await onCreate({ name: trimmedName, description: trimmedDescription })
      setName("")
      setDescription("")
      setError("")
      onClose()
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Unable to create workspace.")
    } finally { savingRef.current = false; setSaving(false) }
  }

  function handleClose() {
    if (savingRef.current) return
    setName("")
    setDescription("")
    setError("")
    onClose()
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/50 p-4">
      <div
        className="w-full max-w-lg rounded-2xl bg-white shadow-xl"
        role="dialog"
        aria-modal="true"
        aria-labelledby="create-workspace-title"
      >
        <div className="flex items-center justify-between border-b border-slate-200 px-5 py-4">
          <div>
            <h2
              id="create-workspace-title"
              className="text-lg font-semibold text-slate-950"
            >
              Create Workspace
            </h2>

            <p className="mt-1 text-sm text-slate-500">
              Create a workspace to organize related datasets,
              processing, and analytics.
            </p>
          </div>

          <button
            type="button"
            disabled={saving} onClick={handleClose}
            className="flex h-9 w-9 items-center justify-center rounded-lg text-slate-500 transition hover:bg-slate-100 hover:text-slate-900"
            aria-label="Close"
          >
            <X size={18} />
          </button>
        </div>

        <form
          onSubmit={handleSubmit}
          className="space-y-5 p-5"
        >
          <div>
            <label
              htmlFor="workspace-name"
              className="mb-2 block text-sm font-medium text-slate-700"
            >
              Workspace name
            </label>

            <Input
              id="workspace-name"
              value={name}
              onChange={(event) => {
                setName(event.target.value)

                if (error) {
                  setError("")
                }
              }}
              placeholder="e.g. Sales Analytics"
              disabled={saving}
              autoFocus
            />

            <p className="mt-2 text-xs text-slate-500">
              Use a name that represents a business area or
              analytics use case.
            </p>
          </div>

          <div>
            <label
              htmlFor="workspace-description"
              className="mb-2 block text-sm font-medium text-slate-700"
            >
              Description
            </label>

            <textarea
              id="workspace-description"
              value={description}
              onChange={(event) =>
                setDescription(event.target.value)
              }
              disabled={saving}
              rows={4}
              placeholder="Describe what this workspace will contain..."
              className="w-full resize-none rounded-lg border border-slate-200 bg-white px-3 py-2.5 text-sm text-slate-900 outline-none transition placeholder:text-slate-400 focus:border-indigo-500 focus:ring-2 focus:ring-indigo-100"
            />
          </div>

          {error && (
            <p className="rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700">
              {error}
            </p>
          )}

          <div className="flex flex-col-reverse gap-3 border-t border-slate-100 pt-4 sm:flex-row sm:justify-end">
            <Button
              type="button"
              variant="secondary"
              disabled={saving} onClick={handleClose}
            >
              Cancel
            </Button>

            <Button type="submit" disabled={saving}>
              Create Workspace
            </Button>
          </div>
        </form>
      </div>
    </div>
  )
}