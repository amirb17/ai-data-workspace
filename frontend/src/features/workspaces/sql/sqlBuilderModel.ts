import type {SqlColumn, SqlBuilderInput, SqlSelection, SqlCatalog} from '../../../services/api/sqlAnalytics'
export const clauses=['SELECT','FROM','JOIN','WHERE','GROUP BY','HAVING','ORDER BY','LIMIT'] as const
export type Clause=typeof clauses[number]
export function columnGroup(c:SqlColumn) {
  if(c.key)return 'Keys'
  if(c.time||['DATE','DATETIME'].includes(c.type))return 'Time'
  if(c.role==='MEASURE')return 'Measures'
  if(['CATEGORY','STATUS','DIMENSION','ENTITY_REFERENCE'].includes(c.role))return 'Dimensions'
  return 'Other'
}
export function columnOptions(columns:SqlColumn[]) {return columns.filter(c=>c.queryable&&!c.sensitive).map(c=>({value:c.name,label:c.name,detail:`${c.type} · ${c.role==='UNKNOWN'?'Meaning not confirmed':c.role.toLowerCase().replaceAll('_',' ')}`,group:columnGroup(c)})).sort((a,b)=>['Keys','Dimensions','Measures','Time','Other'].indexOf(a.group)-['Keys','Dimensions','Measures','Time','Other'].indexOf(b.group))}
export function aggregations(c?:SqlColumn,allRows=false) {
  const values=allRows?['COUNT']:['','COUNT','COUNT_DISTINCT',...(['INTEGER','DECIMAL'].includes(c?.type||'')?['SUM','AVG','MIN','MAX']:['DATE','DATETIME'].includes(c?.type||'')?['MIN','MAX']:[])]
  return values.map(value=>({value,label:value?value.replace('_',' '):'No aggregation'}))
}
export function filterOperators(c?:SqlColumn) {
  const date=['DATE','DATETIME'].includes(c?.type||'')
  return [{value:'EQ',label:date?'On':'Equals'},{value:'NE',label:'Not equal'},...(['INTEGER','DECIMAL','DATE','DATETIME'].includes(c?.type||'')?[{value:'GT',label:date?'After':'Greater than'},{value:'GE',label:'At least'},{value:'LT',label:date?'Before':'Less than'},{value:'LE',label:'At most'},{value:'BETWEEN',label:'Between'}]:c?.type==='TEXT'?[{value:'CONTAINS',label:'Contains'},{value:'STARTS',label:'Starts with'}]:[]),{value:'IS_NULL',label:'Is null'},{value:'IS_NOT_NULL',label:'Is not null'}]
}
export function inputType(c?:SqlColumn) {return ['INTEGER','DECIMAL'].includes(c?.type||'')?'number':c?.type==='DATE'?'date':c?.type==='DATETIME'?'datetime-local':'text'}
export function outputName(s:SqlSelection) {return s.alias|| (s.aggregation?`${s.aggregation.toLowerCase()}_${s.column||'rows'}`:s.column||'')}
export function emptyBuilder(dataset=''):SqlBuilderInput {return {dataset,selections:[],columns:[],grouping:[],filters:[],descending:false,limit:100}}
export function builderReady(input:SqlBuilderInput) {return !!input.dataset&&!!input.selections?.length&&input.selections.every(s=>(s.column||s.aggregation==='COUNT')&&(!s.aggregation||s.aggregation==='COUNT'||s.column))&&input.filters.every(f=>f.column&&(['IS_NULL','IS_NOT_NULL'].includes(f.operator)||f.value!==''&&f.value!==undefined)&&(f.operator!=='BETWEEN'||!!f.end))}

export function reconcileBuilder(input:SqlBuilderInput,catalog:SqlCatalog):SqlBuilderInput {
  const source=catalog.datasets.find(d=>d.alias===input.dataset&&d.queryable)
  if(!source)return input.dataset?emptyBuilder():input
  const columns=source.columns.filter(c=>c.queryable&&!c.sensitive),valid=new Set(columns.map(c=>c.name))
  const selections=input.selections?.filter(s=>!s.column||valid.has(s.column)).map(s=>aggregations(columns.find(c=>c.name===s.column),!s.column).some(a=>a.value===(s.aggregation||''))?s:{...s,aggregation:undefined,alias:undefined})
  const filters=input.filters.filter(f=>valid.has(f.column)&&filterOperators(columns.find(c=>c.name===f.column)).some(o=>o.value===f.operator))
  const next={...input,selections,filters,grouping:input.grouping.filter(c=>valid.has(c)),sort_by:selections?.some(s=>outputName(s)===input.sort_by)?input.sort_by:undefined,relationship_id:catalog.relationships.some(r=>r.relationship_id===input.relationship_id&&r.child.dataset_id===source.dataset_id&&catalog.datasets.some(d=>d.dataset_id===r.parent.dataset_id&&d.queryable))?input.relationship_id:undefined}
  return JSON.stringify(next)===JSON.stringify(input)?input:next
}
