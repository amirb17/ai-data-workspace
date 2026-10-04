import { ProcessingPipeline } from "../../features/home/components/ProcessingPipeline"
import { RecentDatasets } from "../../features/home/components/RecentDatasets"
import { WelcomeHero } from "../../features/home/components/WelcomeHero"
import { WorkspaceStats } from "../../features/home/components/WorkspaceStats"
import { homeDashboardMock } from "../../features/home/data/home.mock"

export function HomePage() {
  return (
    <div className="mx-auto max-w-[1600px] space-y-6">
      <WelcomeHero />

      <WorkspaceStats
        stats={homeDashboardMock.stats}
      />

      <section className="grid gap-6 xl:grid-cols-[minmax(0,1.6fr)_minmax(340px,0.8fr)]">
        <RecentDatasets
          datasets={
            homeDashboardMock.recentDatasets
          }
        />

        <ProcessingPipeline
          stages={
            homeDashboardMock.processingStages
          }
        />
      </section>
    </div>
  )
}