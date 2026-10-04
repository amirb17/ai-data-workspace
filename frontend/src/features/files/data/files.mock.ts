import type { DatasetFile } from "../types"

export const filesByDatasetMock: Record<
  string,
  DatasetFile[]
> = {
  "1": [
    {
      id: 1,
      workspaceId: 1,
      datasetId: 1,
      fileName: "orders_2026_01.csv",
      fileType: "CSV",
      sizeBytes: 2457600,
      status: "PROCESSED",
      uploadedAt: "2 hours ago",
      rowCount: 12450,
      columnCount: 18,
      batchId: 1,
    },
    {
      id: 2,
      workspaceId: 1,
      datasetId: 1,
      fileName: "orders_2026_02.csv",
      fileType: "CSV",
      sizeBytes: 2735000,
      status: "READY_TO_PROCESS",
      uploadedAt: "15 minutes ago",
      rowCount: 13180,
      columnCount: 18,
      batchId: 2,
    },
  ],
}