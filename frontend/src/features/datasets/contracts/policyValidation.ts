export function businessKeyError(strategy: string, keys: string, columns: string[]) {
  const selected = keys.trim() ? keys.split(",").map(k => k.trim()) : []
  if (["UPSERT","SNAPSHOT"].includes(strategy) && !selected.length) return "Choose at least one business key column."
  if (new Set(selected).size !== selected.length || selected.some(k => !columns.includes(k))) return "Use distinct key columns from the selected schema."
  return ""
}
