import type { ReactNode } from "react"

import { Card } from "./Card"

type StatCardProps = {
  label: string
  value: string | number
  helper?: string
  icon?: ReactNode
}

export function StatCard({
  label,
  value,
  helper,
  icon,
}: StatCardProps) {
  return (
    <Card className="p-5">
      <div className="flex items-start justify-between gap-4">
        <div>
          <p className="text-sm font-medium text-slate-500">
            {label}
          </p>

          <p className="mt-2 text-2xl font-semibold tracking-tight text-slate-950">
            {value}
          </p>

          {helper && (
            <p className="mt-1 text-xs text-slate-500">
              {helper}
            </p>
          )}
        </div>

        {icon && (
          <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-indigo-50 text-indigo-600">
            {icon}
          </div>
        )}
      </div>
    </Card>
  )
}