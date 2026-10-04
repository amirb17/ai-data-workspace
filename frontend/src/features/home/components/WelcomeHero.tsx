import {
  ArrowRight,
  Play,
  Upload,
} from "lucide-react"

import { Button } from "../../../components/ui/Button"

export function WelcomeHero() {
  return (
    <section className="relative overflow-hidden rounded-2xl border border-indigo-100 bg-gradient-to-br from-indigo-50 via-white to-violet-50 px-6 py-8 sm:px-8 lg:px-10">
      <div className="relative z-10 max-w-2xl">
        <p className="text-xs font-semibold uppercase tracking-[0.16em] text-indigo-600">
          Welcome to DataRise AI
        </p>

        <h1 className="mt-3 max-w-xl text-3xl font-semibold tracking-tight text-slate-950 sm:text-4xl lg:text-5xl">
          Turn raw data into{" "}
          <span className="text-indigo-600">
            trusted insight.
          </span>
        </h1>

        <p className="mt-4 max-w-xl text-sm leading-6 text-slate-600 sm:text-base">
          Upload your dataset, process it through a reliable data
          pipeline, and start exploring analytics in minutes.
        </p>

        <div className="mt-6 flex flex-col gap-3 sm:flex-row">
          <Button className="min-h-11">
            <Upload size={17} />
            Upload Dataset
          </Button>

          <Button
            variant="secondary"
            className="min-h-11"
          >
            <Play size={16} />
            See How It Works
          </Button>
        </div>
      </div>

      <div className="pointer-events-none absolute -right-20 -top-24 h-72 w-72 rounded-full bg-indigo-200/30 blur-3xl" />

      <div className="pointer-events-none absolute bottom-5 right-5 hidden items-center gap-2 rounded-xl border border-white/80 bg-white/80 px-4 py-3 shadow-sm backdrop-blur lg:flex">
        <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-indigo-50 text-indigo-600">
          <ArrowRight size={18} />
        </div>

        <div>
          <p className="text-xs font-medium text-slate-500">
            Workflow
          </p>

          <p className="text-sm font-semibold text-slate-900">
            Upload → Process → Explore
          </p>
        </div>
      </div>
    </section>
  )
}