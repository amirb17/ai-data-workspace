import type {SqlCell} from '../../../services/api/sqlAnalytics'

export function cellText(value:SqlCell) {return value===null?'NULL':String(value)}
export function numeric(type:string) {return /^(U?TINYINT|U?SMALLINT|U?INTEGER|U?BIGINT|HUGEINT|DECIMAL|FLOAT|DOUBLE)/.test(type)}
export function compareCells(a:SqlCell,b:SqlCell,type:string) {
  if(a===null||b===null)return a===b?0:a===null?1:-1
  if(numeric(type)) {
    const left=String(a),right=String(b)
    if(/^-?\d+(\.\d+)?$/.test(left)&&/^-?\d+(\.\d+)?$/.test(right)) {
      const scale=Math.max(left.split('.')[1]?.length||0,right.split('.')[1]?.length||0)
      const exact=(v:string)=>{const [whole,fraction='']=v.split('.');return BigInt(whole+fraction.padEnd(scale,'0'))}
      const x=exact(left),y=exact(right);return x<y?-1:x>y?1:0
    }
    return Number(a)-Number(b)
  }
  return String(a).localeCompare(String(b))
}
