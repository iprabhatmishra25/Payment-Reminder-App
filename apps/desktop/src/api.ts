export type ApiRequest = {path:string; method?:string; body?:unknown; key?:string};
declare global {interface Window {duewise?:{request:(request:ApiRequest)=>Promise<any>; saveCsv:(csv:string,name:string)=>Promise<boolean>;serverInfo:()=>Promise<string>;setServer:(origin:string)=>Promise<string>}}}
let access = '', refresh = '';
let refreshing: Promise<void> | null = null;
const base = 'http://127.0.0.1:8000';
function friendly(value:any):string {
  if (Array.isArray(value)) return value.map(item=>`${item.loc?.slice(-1).join('') || 'Input'}: ${item.msg}`).join('; ');
  return typeof value==='string' ? value : 'The request could not be completed';
}
export async function api(path:string, options:Omit<ApiRequest,'path'>={}):Promise<any> {
  if(window.duewise) return window.duewise.request({path,...options});
  const send=()=>fetch(base+path,{method:options.method||'GET',headers:{'Content-Type':'application/json',...(access?{Authorization:`Bearer ${access}`}:{ }),...(options.key?{'Idempotency-Key':options.key}:{})},body:options.body===undefined?undefined:JSON.stringify(options.body)});
  let result:Response;
  try {result=await send();} catch {throw new Error('Cannot reach the backend. Start the API on 127.0.0.1:8000.');}
  if(result.status===401 && refresh && !path.includes('/auth/')) {
    if(!refreshing) refreshing=(async()=>{
      const response=await fetch(base+'/api/v1/auth/refresh',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({refresh_token:refresh})});
      if(!response.ok) {access=refresh=''; throw new Error('Session expired. Please sign in again.');}
      const data=await response.json(); access=data.access_token; refresh=data.refresh_token;
    })().finally(()=>{refreshing=null;});
    await refreshing; result=await send();
  }
  if(path.endsWith('.csv') || path.includes('/export.csv?')) {
    if(!result.ok) throw new Error(friendly((await result.json()).detail));
    return result.text();
  }
  const data=await result.json();
  if(!result.ok) throw new Error(friendly(data.detail));
  if(data.access_token) {access=data.access_token;refresh=data.refresh_token;}
  if(path.endsWith('/logout')) access=refresh='';
  return data;
}
export async function saveCsv(csv:string, filename:string) {
  if(window.duewise) return window.duewise.saveCsv(csv,filename);
  const url=URL.createObjectURL(new Blob([csv],{type:'text/csv'})); const a=document.createElement('a');a.href=url;a.download=filename;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);return true;
}
export const rupees=(value:number|string,compact=false)=>new Intl.NumberFormat('en-IN',{style:'currency',currency:'INR',maximumFractionDigits:compact?1:0,...(compact?{notation:'compact'}:{})}).format(Number(value));
export const dateLabel=(value:string)=>new Date(value.length===10?value+'T12:00:00':value).toLocaleDateString('en-IN',{day:'numeric',month:'short',year:'numeric'});
export const monthLabel=(value:string)=>new Date(value+'-01T12:00:00').toLocaleDateString('en-IN',{month:'long',year:'numeric'});
