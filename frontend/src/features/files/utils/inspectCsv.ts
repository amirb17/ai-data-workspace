import type { CsvInspectionResult } from "../types"

// Parse records rather than physical lines: quoted fields may contain newlines.
function parseCsv(text: string): string[][] {
  const records: string[][] = []
  let row: string[] = []
  let value = ""
  let quoted = false
  let closedQuote = false
  function finishField() {
    row.push(value)
    value = ""
    closedQuote = false
  }
  function finishRow() {
    finishField()
    if (row.length > 1 || row[0].trim()) records.push(row)
    row = []
  }
  for (let i = 0; i < text.length; i += 1) {
    const char = text[i]
    if (quoted) {
      if (char === '"') {
        if (text[i + 1] === '"') { value += '"'; i += 1 }
        else { quoted = false; closedQuote = true }
      } else value += char
    } else if (char === ',') finishField()
    else if (char === '\n' || char === '\r') {
      if (char === '\r' && text[i + 1] === '\n') i += 1
      finishRow()
    } else if (char === '"' && value === "" && !closedQuote) quoted = true
    else {
      if (char === '"' || (closedQuote && char.trim())) {
        throw new Error("The CSV contains malformed quoted fields.")
      }
      if (!closedQuote) value += char
    }
  }
  if (quoted) throw new Error("The CSV contains an unclosed quoted field.")
  finishRow()
  return records
}

export async function inspectCsv(file: File): Promise<CsvInspectionResult> {
  const records = parseCsv((await file.text()).replace(/^\uFEFF/, ""))
  if (!records.length) throw new Error("The CSV file is empty.")
  const columns = records[0].map((column) => column.trim())
  if (columns.some((column) => !column)) {
    throw new Error("The CSV contains empty column names.")
  }
  if (new Set(columns.map((column) => column.toLowerCase())).size !== columns.length) {
    throw new Error("The CSV contains duplicate column names.")
  }
  const rows = records.slice(1)
  if (!rows.length) throw new Error("The CSV contains headers but no data rows.")
  const invalidRow = rows.findIndex((row) => row.length !== columns.length)
  if (invalidRow !== -1) {
    throw new Error(`CSV data row ${invalidRow + 1} has a different number of fields than the header.`)
  }
  return {
    fileName: file.name,
    sizeBytes: file.size,
    fileType: "CSV",
    rowCount: rows.length,
    columnCount: columns.length,
    columns,
    previewRows: rows.slice(0, 5).map((row) => Object.fromEntries(columns.map((column, i) => [column, row[i]]))),
  }
}
