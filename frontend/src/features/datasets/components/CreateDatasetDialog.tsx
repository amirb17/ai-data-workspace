import { X } from "lucide-react"
import { useRef, useState } from "react"

import { Button } from "../../../components/ui/Button"
import { Input } from "../../../components/ui/Input"

type CreateDatasetDialogProps = {
  open: boolean
  onClose: () => void
  onCreate: (dataset: {
    name: string
    description: string
  }) => Promise<void>
}

export function CreateDatasetDialog({
  open,
  onClose,
  onCreate,
}: CreateDatasetDialogProps) {
  const [name, setName] = useState("")
  const [description, setDescription] = useState("")
  const savingRef = useRef(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState("")

  if (!open) {
    return null
  }

  function resetForm() {
    setName("")
    setDescription("")
    setError("")
  }

  function handleClose() {
    if (savingRef.current) return
    resetForm()
    onClose()
  }

  async function handleSubmit(
    event: React.FormEvent<HTMLFormElement>,
  ) {
    event.preventDefault()
    if (savingRef.current) return

    const trimmedName = name.trim()
    const trimmedDescription = description.trim()

    if (!trimmedName) {
      setError("Dataset name is required.")
      return
    }

    if (trimmedName.length < 3) {
      setError(
        "Dataset name must be at least 3 characters.",
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
      setError(caught instanceof Error ? caught.message : "Unable to create dataset.")
    } finally { savingRef.current = false; setSaving(false) }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/50 p-4">
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="create-dataset-title"
        className="w-full max-w-lg rounded-2xl bg-white shadow-xl"
      >
        <div className="flex items-start justify-between border-b border-slate-200 px-5 py-4">
          <div>
            <h2
              id="create-dataset-title"
              className="text-lg font-semibold text-slate-950"
            >
              Create Dataset
            </h2>

            <p className="mt-1 text-sm text-slate-500">
              Add a dataset to this workspace.
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
              htmlFor="dataset-name"
              className="mb-2 block text-sm font-medium text-slate-700"
            >
              Dataset name
            </label>

            <Input
              id="dataset-name"
              value={name}
              onChange={(event) => {
                setName(event.target.value)

                if (error) {
                  setError("")
                }
              }}
              placeholder="e.g. Customer Orders"
              disabled={saving}
              autoFocus
            />
          </div>

          <div>
            <label
              htmlFor="dataset-description"
              className="mb-2 block text-sm font-medium text-slate-700"
            >
              Description
            </label>

            <textarea
              id="dataset-description"
              value={description}
              onChange={(event) =>
                setDescription(event.target.value)
              }
              disabled={saving}
              rows={4}
              placeholder="Describe what this dataset contains..."
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
              Create Dataset
            </Button>
          </div>
        </form>
      </div>
    </div>
  )
}