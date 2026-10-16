import type {SqlDataset} from '../../../services/api/sqlAnalytics'
export const quoteIdentifier=(name:string)=>'"'+name.replaceAll('"','""')+'"'
export function sqlTemplates(dataset:SqlDataset) {
  if(!dataset.queryable)return []
  const alias=quoteIdentifier(dataset.alias)
  const templates=[{name:'Preview dataset',sql:`SELECT *\nFROM ${alias}\nLIMIT 20;`},{name:'Count rows',sql:`SELECT COUNT(*) AS row_count\nFROM ${alias};`}]
  const category=dataset.columns.find(c=>c.queryable&&c.type==='TEXT'&&['CATEGORY','STATUS'].includes(c.role))
  if(category){const col=quoteIdentifier(category.name);templates.push({name:'Count by '+category.name,sql:`SELECT ${col}, COUNT(*) AS row_count\nFROM ${alias}\nGROUP BY ${col}\nORDER BY row_count DESC;`})}
  const time=dataset.columns.find(c=>c.queryable&&c.time&&['DATE','DATETIME'].includes(c.type))
  if(time)templates.push({name:'Recent records by '+time.name,sql:`SELECT *\nFROM ${alias}\nORDER BY ${quoteIdentifier(time.name)} DESC\nLIMIT 20;`})
  return templates
}
