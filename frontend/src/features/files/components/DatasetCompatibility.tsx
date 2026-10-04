import type { SchemaMatchResult } from "../../datasets/contracts/schemaMatch"

export function DatasetCompatibility({ schemaMatch }: { schemaMatch: SchemaMatchResult }) {
  return (
<section className="rounded-xl border border-slate-200 p-4">
    <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
      <div>
        <h3 className="text-sm font-semibold text-slate-900">
          Dataset Compatibility
        </h3>

        <p className="mt-1 text-sm text-slate-500">
          DataRise compared the incoming columns with the trusted dataset structure.
        </p>
      </div>

      {schemaMatch.status !==
        "NO_CONTRACT" && (
        <div className="rounded-lg bg-slate-100 px-3 py-2 text-sm font-semibold text-slate-900">
          {
            schemaMatch.matchPercentage
          }
          % match
        </div>
      )}
    </div>

    <div className="mt-4">
      {schemaMatch.status ===
        "NO_CONTRACT" && (
        <div className="rounded-lg bg-blue-50 p-4">
          <p className="text-sm font-semibold text-blue-900">
            No dataset contract yet
          </p>

          <p className="mt-1 text-sm leading-6 text-blue-700">
            Configure types, required fields, business keys, load mode, and schema policy before accepting this file.
          </p>
        </div>
      )}

      {schemaMatch.status ===
        "MATCH" && (
        <div className="rounded-lg bg-green-50 p-4">
          <p className="text-sm font-semibold text-green-800">
            Schema matches
          </p>

          <p className="mt-1 text-sm text-green-700">
            The incoming file matches the current dataset contract.
          </p>
        </div>
      )}

      {schemaMatch.status ===
        "WARNING" && (
        <div className="rounded-lg bg-amber-50 p-4">
          <p className="text-sm font-semibold text-amber-800">
            Compatible with warnings
          </p>

          <p className="mt-1 text-sm text-amber-700">
            The required fields are present, but additional or missing optional columns differ from the contract.
          </p>
        </div>
      )}

      {schemaMatch.status ===
        "BREAKING" && (
        <div className="rounded-lg bg-red-50 p-4">
          <p className="text-sm font-semibold text-red-800">
            Breaking schema change
          </p>

          <p className="mt-1 text-sm text-red-700">
            Required or key columns are missing, or additional columns violate the STRICT policy. This file cannot be accepted.
          </p>
        </div>
      )}

      {schemaMatch.status ===
        "WRONG_DATASET_LIKELY" && (
        <div className="rounded-lg bg-red-50 p-4">
          <p className="text-sm font-semibold text-red-800">
            This may be the wrong dataset
          </p>

          <p className="mt-1 text-sm leading-6 text-red-700">
            The incoming structure is significantly different from the trusted structure of this dataset.
          </p>
        </div>
      )}
    </div>

    {schemaMatch.missingRequiredColumns.length >
      0 && (
      <div className="mt-4">
        <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">
          Missing required columns
        </p>

        <div className="mt-2 flex flex-wrap gap-2">
          {schemaMatch.missingRequiredColumns.map(
            (column) => (
              <span
                key={column}
                className="rounded-md bg-red-50 px-2.5 py-1 text-xs font-medium text-red-700"
              >
                {column}
              </span>
            ),
          )}
        </div>
      </div>
    )}

    {schemaMatch.unexpectedColumns.length >
      0 &&
      schemaMatch.status !==
        "NO_CONTRACT" && (
        <div className="mt-4">
          <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">
            Additional columns
          </p>

          <div className="mt-2 flex flex-wrap gap-2">
            {schemaMatch.unexpectedColumns.map(
              (column) => (
                <span
                  key={column}
                  className="rounded-md bg-amber-50 px-2.5 py-1 text-xs font-medium text-amber-700"
                >
                  {column}
                </span>
              ),
            )}
          </div>
        </div>
      )}
  </section>
  )
}
