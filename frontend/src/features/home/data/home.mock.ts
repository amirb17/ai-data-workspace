import type { HomeDashboardData } from "../types"

export const homeDashboardMock: HomeDashboardData = {
  stats: [
    {
      id: "datasets",
      label: "Total Datasets",
      value: 12,
      helper: "+3 this week",
      status: "success",
    },
    {
      id: "processing",
      label: "Processing Runs",
      value: 24,
      helper: "3 active",
      status: "warning",
    },
    {
      id: "analytics",
      label: "Analytics Ready",
      value: 9,
      helper: "+2 this week",
      status: "success",
    },
    {
      id: "quality",
      label: "Data Quality Issues",
      value: 4,
      helper: "2 need attention",
      status: "error",
    },
  ],

  recentDatasets: [
    {
      id: 1,
      name: "sales_data.csv",
      status: "READY",
      rows: 12450,
      columns: 18,
      updatedAt: "2 hours ago",
    },
    {
      id: 2,
      name: "customers.xlsx",
      status: "PROCESSING",
      rows: 8230,
      columns: 12,
      updatedAt: "10 minutes ago",
    },
    {
      id: 3,
      name: "orders_2026.csv",
      status: "READY",
      rows: 24580,
      columns: 22,
      updatedAt: "1 day ago",
    },
    {
      id: 4,
      name: "products.csv",
      status: "FAILED",
      rows: 3120,
      columns: 15,
      updatedAt: "2 days ago",
    },
  ],

  processingStages: [
    {
      id: "upload",
      label: "File Uploaded",
      description: "Completed",
      status: "COMPLETED",
    },
    {
      id: "bronze",
      label: "Bronze Layer",
      description: "Raw data stored safely",
      status: "COMPLETED",
    },
    {
      id: "validation",
      label: "Data Validation",
      description: "Schema and quality checks",
      status: "COMPLETED",
    },
    {
      id: "silver",
      label: "Silver Layer",
      description: "Cleaning and transformation",
      status: "RUNNING",
    },
    {
      id: "gold",
      label: "Gold Layer",
      description: "Waiting for Silver",
      status: "PENDING",
    },
    {
      id: "analytics",
      label: "Analytics Ready",
      description: "KPIs, charts and questions",
      status: "PENDING",
    },
  ],
}