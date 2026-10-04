import {
  BarChart3,
  Database,
  ShieldAlert,
  Workflow,
} from "lucide-react"

import { StatCard } from "../../../components/ui/StatCard"
import type { WorkspaceStat } from "../types"

type WorkspaceStatsProps = {
  stats: WorkspaceStat[]
}

const icons = {
  datasets: Database,
  processing: Workflow,
  analytics: BarChart3,
  quality: ShieldAlert,
}

export function WorkspaceStats({
  stats,
}: WorkspaceStatsProps) {
  return (
    <section className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
      {stats.map((stat) => {
        const Icon =
          icons[stat.id as keyof typeof icons]

        return (
          <StatCard
            key={stat.id}
            label={stat.label}
            value={stat.value}
            helper={stat.helper}
            icon={
              Icon ? (
                <Icon size={20} />
              ) : undefined
            }
          />
        )
      })}
    </section>
  )
}