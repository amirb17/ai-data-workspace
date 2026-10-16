import { Link } from "react-router-dom"

export function SettingsPage() {
  return (
    <div>
      <h1 className="text-3xl font-semibold text-slate-950">
        Settings
      </h1>
      <p className="mt-3 max-w-2xl text-sm text-slate-600">
        Account settings are not available in this version. Dataset rules and load policies are managed inside each dataset.
      </p>
      <Link className="mt-5 inline-flex min-h-11 items-center rounded-lg bg-indigo-600 px-4 text-sm text-white" to="/app/workspaces">
        Open workspaces
      </Link>
    </div>
  )
}
