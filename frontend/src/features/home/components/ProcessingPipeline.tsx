import {
  Check,
  Circle,
  LoaderCircle,
  TriangleAlert,
} from "lucide-react"

import { Card } from "../../../components/ui/Card"
import type {
  ProcessingStage,
  ProcessingStageStatus,
} from "../types"

type ProcessingPipelineProps = {
  stages: ProcessingStage[]
}

function StageIcon({
  status,
}: {
  status: ProcessingStageStatus
}) {
  if (status === "COMPLETED") {
    return (
      <div className="flex h-8 w-8 items-center justify-center rounded-full bg-green-100 text-green-700">
        <Check size={16} />
      </div>
    )
  }

  if (status === "RUNNING") {
    return (
      <div className="flex h-8 w-8 items-center justify-center rounded-full bg-blue-100 text-blue-700">
        <LoaderCircle
          size={16}
          className="animate-spin"
        />
      </div>
    )
  }

  if (status === "FAILED") {
    return (
      <div className="flex h-8 w-8 items-center justify-center rounded-full bg-red-100 text-red-700">
        <TriangleAlert size={16} />
      </div>
    )
  }

  return (
    <div className="flex h-8 w-8 items-center justify-center rounded-full border border-slate-200 bg-white text-slate-400">
      <Circle size={12} />
    </div>
  )
}

function getStatusTextClass(
  status: ProcessingStageStatus,
) {
  switch (status) {
    case "COMPLETED":
      return "text-green-600"

    case "RUNNING":
      return "text-blue-600"

    case "FAILED":
      return "text-red-600"

    default:
      return "text-slate-400"
  }
}

export function ProcessingPipeline({
  stages,
}: ProcessingPipelineProps) {
  return (
    <Card className="h-full p-5">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-base font-semibold text-slate-950">
            Processing Pipeline
          </h2>

          <p className="mt-1 text-xs text-slate-500">
            Latest dataset processing status
          </p>
        </div>

        <button className="text-sm font-medium text-indigo-600 hover:text-indigo-700">
          View details
        </button>
      </div>

      <div className="mt-6">
        {stages.map((stage, index) => {
          const isLast =
            index === stages.length - 1

          return (
            <div
              key={stage.id}
              className="relative flex gap-4 pb-5 last:pb-0"
            >
              {!isLast && (
                <div className="absolute left-[15px] top-8 h-[calc(100%-24px)] w-px bg-slate-200" />
              )}

              <div className="relative z-10 shrink-0">
                <StageIcon
                  status={stage.status}
                />
              </div>

              <div className="min-w-0 pt-0.5">
                <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                  <p className="text-sm font-semibold text-slate-900">
                    {stage.label}
                  </p>

                  <span
                    className={[
                      "text-xs font-medium",
                      getStatusTextClass(
                        stage.status,
                      ),
                    ].join(" ")}
                  >
                    {stage.status
                      .toLowerCase()
                      .replace(/\b\w/g, (value) =>
                        value.toUpperCase(),
                      )}
                  </span>
                </div>

                <p className="mt-1 text-xs leading-5 text-slate-500">
                  {stage.description}
                </p>
              </div>
            </div>
          )
        })}
      </div>
    </Card>
  )
}