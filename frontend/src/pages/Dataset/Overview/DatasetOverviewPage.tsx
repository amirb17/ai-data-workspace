import {
  Database,
  FileCheck2,
  ShieldCheck,
  Sparkles,
} from "lucide-react"

export function DatasetOverviewPage() {
  return (
    <div className="grid gap-6 xl:grid-cols-[minmax(0,1.5fr)_minmax(320px,0.7fr)]">
      <section className="rounded-xl border border-slate-200 bg-white p-6">
        <div className="flex items-center gap-3">
          <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-indigo-50 text-indigo-600">
            <Database size={19} />
          </div>

          <div>
            <h2 className="text-lg font-semibold text-slate-950">
              Dataset Overview
            </h2>

            <p className="text-sm text-slate-500">
              Summary of the current dataset state.
            </p>
          </div>
        </div>

        <div className="mt-6 grid gap-4 sm:grid-cols-3">
          <div className="rounded-lg border border-slate-100 bg-slate-50 p-4">
            <FileCheck2
              size={18}
              className="text-green-600"
            />

            <p className="mt-3 text-sm font-medium text-slate-900">
              Latest File
            </p>

            <p className="mt-1 text-xs text-slate-500">
              File information will appear here.
            </p>
          </div>

          <div className="rounded-lg border border-slate-100 bg-slate-50 p-4">
            <ShieldCheck
              size={18}
              className="text-indigo-600"
            />

            <p className="mt-3 text-sm font-medium text-slate-900">
              Data Quality
            </p>

            <p className="mt-1 text-xs text-slate-500">
              Validation summary will appear here.
            </p>
          </div>

          <div className="rounded-lg border border-slate-100 bg-slate-50 p-4">
            <Sparkles
              size={18}
              className="text-amber-500"
            />

            <p className="mt-3 text-sm font-medium text-slate-900">
              Analytics
            </p>

            <p className="mt-1 text-xs text-slate-500">
              Analytics readiness will appear here.
            </p>
          </div>
        </div>
      </section>

      <section className="rounded-xl border border-slate-200 bg-white p-6">
        <h2 className="text-lg font-semibold text-slate-950">
          Workflow
        </h2>

        <div className="mt-5 space-y-4 text-sm">
          {[
            "Upload",
            "Validate",
            "Process",
            "Review Quality",
            "Explore Analytics",
          ].map((step, index) => (
            <div
              key={step}
              className="flex items-center gap-3"
            >
              <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-slate-100 text-xs font-semibold text-slate-600">
                {index + 1}
              </div>

              <span className="text-slate-700">
                {step}
              </span>
            </div>
          ))}
        </div>
      </section>
    </div>
  )
}