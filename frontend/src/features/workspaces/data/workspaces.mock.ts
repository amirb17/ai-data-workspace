import type { WorkspaceListItem } from "../types"

export const workspacesMock: WorkspaceListItem[] = [
  {
    id: 1,
    name: "Sales Analytics",
    description:
      "Orders, customers, products, revenue, and operational sales data.",
    status: "ACTIVE",
    datasetCount: 6,
    analyticsReadyCount: 4,
    processingCount: 1,
    updatedAt: "2 hours ago",
  },
  {
    id: 2,
    name: "Healthcare Analytics",
    description:
      "Operational healthcare datasets, reporting, and data-quality workflows.",
    status: "PROCESSING",
    datasetCount: 3,
    analyticsReadyCount: 1,
    processingCount: 2,
    updatedAt: "15 minutes ago",
  },
  {
    id: 3,
    name: "Finance Analytics",
    description:
      "Transactions, reconciliation, reporting, and financial analytics datasets.",
    status: "ACTIVE",
    datasetCount: 5,
    analyticsReadyCount: 5,
    processingCount: 0,
    updatedAt: "1 day ago",
  },
]