// Public configuration only. Production defaults to a same-origin reverse proxy.
export function resolveApiBase(value:string|undefined,production:boolean):string {
  const base=(value??'').trim().replace(/\/+$/,'')
  if(!base)return production?'':'http://localhost:8000'
  if(/^\/[A-Za-z0-9/_-]*$/.test(base)&&!base.startsWith('//'))return base
  let url:URL
  try{url=new URL(base)}catch{throw new Error('Invalid public API origin configuration.')}
  if(url.username||url.password||url.search||url.hash||url.pathname!=='/'||!['http:','https:'].includes(url.protocol))throw new Error('Invalid public API origin configuration.')
  if(production&&url.protocol!=='https:')throw new Error('Public deployments require HTTPS for the API.')
  if(!production&&url.protocol==='http:'&&!['localhost','127.0.0.1','[::1]'].includes(url.hostname))throw new Error('HTTP API origins are restricted to local development.')
  return url.origin
}
