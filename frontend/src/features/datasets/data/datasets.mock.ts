import type { DatasetListItem } from "../types"

export const datasetsByWorkspaceMock: Record<
  string,
  DatasetListItem[]
> = {
  "1": [
    {
      id: 1,
      name: "Customer Orders",
      description: "Operational order and transaction data",
      status: "READY",
      latestVersion: 7,
      rowCount: 12450,
      columnCount: 18,
      updatedAt: "2 hours ago",
    },
    {
      id: 2,
      name: "Customer Master",
      description: "Customer profiles and reference attributes",
      status: "PROCESSING",
      latestVersion: 3,
      rowCount: 8230,
      columnCount: 12,
      updatedAt: "10 minutes ago",
    },
    {
      id: 3,
      name: "Product Catalogue",
      description: "Product, category, pricing and inventory data",
      status: "READY",
      latestVersion: 5,
      rowCount: 24580,
      columnCount: 22,
      updatedAt: "1 day ago",
    },
  ],

  "2": [
    {
      id: 101,
      name: "Patient Visits",
      description: "Patient visit and operational encounter data",
      status: "READY",
      latestVersion: 4,
      rowCount: 18420,
      columnCount: 16,
      updatedAt: "3 hours ago",
    },
    {
      id: 102,
      name: "Appointment Records",
      description: "Appointment scheduling and attendance data",
      status: "PROCESSING",
      latestVersion: 2,
      rowCount: 9360,
      columnCount: 11,
      updatedAt: "20 minutes ago",
    },
  ],

  "3": [
    {
      id: 201,
      name: "Transactions",
      description: "Financial transaction records for analytics",
      status: "READY",
      latestVersion: 6,
      rowCount: 32800,
      columnCount: 20,
      updatedAt: "5 hours ago",
    },
    {
      id: 202,
      name: "Reconciliation Data",
      description: "Operational reconciliation and settlement data",
      status: "NEEDS_ATTENTION",
      latestVersion: 3,
      rowCount: 11600,
      columnCount: 14,
      updatedAt: "1 day ago",
    },
  ],
}