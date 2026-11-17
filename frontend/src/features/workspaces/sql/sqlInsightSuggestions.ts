import type {SqlCatalog,SqlDataset} from '../../../services/api/sqlAnalytics'
import {quoteIdentifier} from './sqlTemplates'

export interface SqlInsightIdea {
  id:string
  title:string
  description:string
  category:'Business insights'|'Customer insights'|'Explore data'
  sql:string
}

function findColumn(dataset:SqlDataset,...names:string[]) {
  return dataset.columns.find(c=>c.queryable&&names.includes(c.name.toLowerCase()))
}
function numeric(dataset:SqlDataset,...names:string[]) {
  return dataset.columns.find(c=>c.queryable&&['INTEGER','DECIMAL'].includes(c.type)&&names.includes(c.name.toLowerCase()))
}
function makeGroup(dataset:SqlDataset,dimension:string,measure:string,id:string,title:string,description:string,limit=20):SqlInsightIdea{
  const table=quoteIdentifier(dataset.alias)
  const dim=quoteIdentifier(dimension),metric=quoteIdentifier(measure)
  return {id,title,description,category:'Business insights',
    sql:`SELECT ${dim}, COUNT(*) AS order_count, SUM(${metric}) AS total_revenue\nFROM ${table}\nGROUP BY ${dim}\nORDER BY total_revenue DESC\nLIMIT ${limit};`}
}

/** Suggestions use only currently queryable catalog columns and approved joins.
 * No AI-generated SQL, guessed schema, personal-record output or external sources.
 */
export function sqlInsightSuggestions(catalog:SqlCatalog):SqlInsightIdea[] {
  const ready=catalog.datasets.filter(d=>d.queryable)
  const ideas:SqlInsightIdea[]=[]
  const seen=new Set<string>()
  const add=(idea:SqlInsightIdea)=>{if(!seen.has(idea.sql)){seen.add(idea.sql);ideas.push(idea)}}
  for(const dataset of ready){
    const table=quoteIdentifier(dataset.alias)
    const name=dataset.name
    const revenue=numeric(dataset,'revenue_inr','revenue','total_amount','sales_amount','amount')
    const orderId=findColumn(dataset,'order_id')
    const metric=revenue&&quoteIdentifier(revenue.name)
    if(revenue) {
      add({id:`revenue-${dataset.dataset_id}`,title:`Revenue overview · ${name}`,
        description:'Reconcile total revenue, record count and average revenue per row.',
        category:'Business insights',
        sql:`SELECT COUNT(*) AS record_count, SUM(${metric}) AS total_revenue, AVG(${metric}) AS avg_revenue_per_record\nFROM ${table};`})
      for(const [col,title,description] of [
        ['product_category','Revenue by product category','See which product categories contribute most to revenue.'],
        ['region','Revenue by region','Compare regional sales performance and order volume.'],
        ['sales_channel','Revenue by sales channel','Compare revenue across sales channels.'],
        ['order_status','Revenue by order status','Separate completed, pending and other recorded statuses.']
      ] as const) {
        const dimension=findColumn(dataset,col)
        if(dimension) add(makeGroup(dataset,dimension.name,revenue.name,`${col}-${dataset.dataset_id}`,title,description))
      }
      const date=findColumn(dataset,'order_date','transaction_date','sale_date')
      if(date&&['DATE','DATETIME'].includes(date.type)) {
        const dim=quoteIdentifier(date.name)
        add({id:`monthly-${dataset.dataset_id}`,title:'Monthly revenue trend',
          description:'Track monthly revenue and order volume across the available dates.',
          category:'Business insights',
          sql:`SELECT DATE_TRUNC('month', ${dim}) AS sales_month, COUNT(*) AS order_count, SUM(${metric}) AS total_revenue\nFROM ${table}\nGROUP BY DATE_TRUNC('month', ${dim})\nORDER BY sales_month\nLIMIT 100;`})
      }
    }
    if(orderId&&revenue) {
      const dim=findColumn(dataset,'customer_id')
      if(dim) add({id:`customer-contribution-${dataset.dataset_id}`,title:'Top customer IDs by revenue',
        description:'Compare order count and revenue by customer identifier; aggregate results only.',
        category:'Customer insights',
        sql:`SELECT ${quoteIdentifier(dim.name)}, COUNT(*) AS order_count, SUM(${metric}) AS total_revenue\nFROM ${table}\nGROUP BY ${quoteIdentifier(dim.name)}\nORDER BY total_revenue DESC\nLIMIT 20;`})
    }
    add({id:`count-${dataset.dataset_id}`,title:`Count records · ${name}`,
      description:'Check the trusted dataset row count.',
      category:'Explore data',
      sql:`SELECT COUNT(*) AS row_count\nFROM ${table};`})
    add({id:`preview-${dataset.dataset_id}`,title:`Preview · ${name}`,
      description:'Preview the first 20 queryable records in this dataset.',
      category:'Explore data',
      sql:`SELECT *\nFROM ${table}\nLIMIT 20;`})
  }
  for(const relation of catalog.relationships){
    const child=ready.find(d=>d.dataset_id===relation.child.dataset_id)
    const parent=ready.find(d=>d.dataset_id===relation.parent.dataset_id)
    if(!child||!parent||relation.child.columns.length!==1||relation.parent.columns.length!==1||
       !['ONE_TO_MANY','ONE_TO_ONE'].includes(relation.cardinality))continue
    const childKey=child.columns.find(c=>c.name===relation.child.columns[0]&&c.queryable)
    const parentKey=parent.columns.find(c=>c.name===relation.parent.columns[0]&&c.queryable)
    if(!childKey||!parentKey)continue
    const revenue=numeric(child,'revenue_inr','revenue','total_amount','sales_amount','amount')
    if(!revenue)continue
    const left=`FROM ${quoteIdentifier(child.alias)} AS o\nLEFT JOIN ${quoteIdentifier(parent.alias)} AS c\n  ON o.${quoteIdentifier(childKey.name)} = c.${quoteIdentifier(parentKey.name)}`
    for(const [field,title,description] of [
      ['customer_segment','Revenue by customer segment','Compare order volume and revenue across customer segments.'],
      ['loyalty_tier','Revenue by loyalty tier','Analyze revenue and orders across loyalty tiers.'],
      ['acquisition_channel','Revenue by acquisition channel','Compare revenue by the customers’ recorded acquisition channel.']
    ] as const){
      const dimension=findColumn(parent,field)
      if(!dimension)continue
      const dim=`c.${quoteIdentifier(dimension.name)}`
      add({id:`relation-${relation.relationship_id}-${field}`,title,description,category:'Customer insights',
        sql:`SELECT ${dim} AS ${quoteIdentifier(field)}, COUNT(*) AS order_count, SUM(o.${quoteIdentifier(revenue.name)}) AS total_revenue\n${left}\nGROUP BY ${dim}\nORDER BY total_revenue DESC\nLIMIT 20;`})
    }
  }
  return ideas
}
