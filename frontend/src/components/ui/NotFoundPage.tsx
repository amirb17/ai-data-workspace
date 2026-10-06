import { Link } from "react-router-dom"
export function NotFoundPage() {
  return <section className="space-y-3 rounded-xl border border-slate-200 bg-white p-6"><h1 className="text-2xl font-semibold">Page not found</h1><p className="text-slate-600">This page is unavailable. Choose a workspace to continue.</p><Link className="inline-flex min-h-11 items-center rounded-lg bg-indigo-600 px-4 text-white" to="/app/workspaces">Back to Workspaces</Link></section>
}
