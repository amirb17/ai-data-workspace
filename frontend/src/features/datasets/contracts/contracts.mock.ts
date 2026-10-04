import type { DatasetContract } from "./types"

export const datasetContractsMock: Record<
  string,
  DatasetContract
> = {
  "1": {
    datasetId: 1,
    datasetName: "Customer Orders",

    columns: [
      {
        name: "order_id",
        dataType: "STRING",
        required: true,
      },
      {
        name: "customer_id",
        dataType: "STRING",
        required: true,
      },
      {
        name: "order_date",
        dataType: "DATE",
        required: true,
      },
      {
        name: "amount",
        dataType: "DECIMAL",
        required: true,
      },
      {
        name: "status",
        dataType: "STRING",
        required: false,
      },
    ],

    primaryKey: [
      "order_id",
    ],

    loadMode: "UPSERT",
  },

  "2": {
    datasetId: 2,
    datasetName: "Customer Master",

    columns: [
      {
        name: "customer_id",
        dataType: "STRING",
        required: true,
      },
      {
        name: "name",
        dataType: "STRING",
        required: true,
      },
      {
        name: "email",
        dataType: "STRING",
        required: false,
      },
      {
        name: "city",
        dataType: "STRING",
        required: false,
      },
      {
        name: "segment",
        dataType: "STRING",
        required: false,
      },
    ],

    primaryKey: [
      "customer_id",
    ],

    loadMode: "UPSERT",
  },
}