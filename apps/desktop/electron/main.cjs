const {app,BrowserWindow,ipcMain,protocol,net,dialog,session}=require('electron');
const path=require('node:path');
const fs=require('node:fs/promises');
const {pathToFileURL}=require('node:url');
const allowedPath=/^\/(health\/(live|ready)|api\/v1\/(auth\/(login|logout|me|refresh|mfa\/(verify|setup|confirm)|forgot-password|reset-password|sessions(?:\/[a-zA-Z0-9-]+)?)|meta|companies(?:\/[a-zA-Z0-9-]+)?|invoices(?:\/[a-zA-Z0-9-]+(?:\/(payments|post|void))?)?|payments(?:\/[a-zA-Z0-9-]+\/reverse)?|reminder-policy|reminder-runs(?:\/(?:preview|[a-zA-Z0-9-]+))?|reminders(?:\/[a-zA-Z0-9-]+\/(?:retry|resolve))?|reports\/(monthly(?:\/export\.csv)?|trend)|operations\/status|audit-logs|users(?:\/[a-zA-Z0-9-]+)?))$/;
function validateOrigin(value){const url=new URL(value);if(url.protocol!=='https:'&&!(url.protocol==='http:'&&['127.0.0.1','localhost','[::1]'].includes(url.hostname)))throw new Error('Remote server addresses must use HTTPS');if(url.username||url.password||url.pathname!=='/'||url.search||url.hash)throw new Error('Use a server origin without a path or credentials');return url;}
let base=validateOrigin(process.env.DUEWISE_API_URL||'http://127.0.0.1:8000');
protocol.registerSchemesAsPrivileged([{scheme:'duewise',privileges:{standard:true,secure:true,supportFetchAPI:true}}]);
let window, access='',refresh='',refreshing=null;
function validSender(event) {if(!window||event.sender!==window.webContents||event.senderFrame!==window.webContents.mainFrame) throw new Error('Invalid IPC sender');}
function errorMessage(detail){return Array.isArray(detail)?detail.map(x=>x.msg).join('; '):typeof detail==='string'?detail:'Request failed';}
async function request(input) {
  if(!input||typeof input.path!=='string'||input.path.length>2000) throw new Error('Invalid request');
  const url=new URL(input.path,base);
  if(url.origin!==base.origin||!allowedPath.test(url.pathname)||!['GET','POST','PUT','PATCH','DELETE'].includes(input.method||'GET')) throw new Error('Unsupported API operation');
  const body=input.body===undefined?undefined:JSON.stringify(input.body);
  if(body&&body.length>100000) throw new Error('Request too large');
  const send=()=>net.fetch(url.href,{method:input.method||'GET',headers:{'Content-Type':'application/json',...(access?{Authorization:`Bearer ${access}`}:{ }),...(input.key?{'Idempotency-Key':String(input.key)}:{})},body,signal:AbortSignal.timeout(30000)});
  let response;
  try {response=await send();} catch {throw new Error('Cannot reach the API. Check your server connection.');}
  if(response.status===401&&refresh&&!url.pathname.includes('/auth/')) {
    if(!refreshing) refreshing=(async()=>{
      const r=await net.fetch(new URL('/api/v1/auth/refresh',base).href,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({refresh_token:refresh}),signal:AbortSignal.timeout(15000)});
      if(!r.ok) {access=refresh='';throw new Error('Session expired. Sign in again.');}
      const data=await r.json();access=data.access_token;refresh=data.refresh_token;
    })().finally(()=>{refreshing=null;});
    await refreshing;response=await send();
  }
  if(url.pathname.endsWith('.csv')&&response.ok) return response.text();
  const data=await response.json();
  if(!response.ok) throw new Error(errorMessage(data.detail));
  if(data.access_token) {access=data.access_token;refresh=data.refresh_token;delete data.access_token;delete data.refresh_token;}
  if(url.pathname.endsWith('/logout')) access=refresh='';
  return data;
}
app.whenReady().then(async()=>{
  const connectionFile=path.join(app.getPath('userData'),'connection.json');
  if(!process.env.DUEWISE_API_URL){try{base=validateOrigin(JSON.parse(await fs.readFile(connectionFile,'utf8')).origin);}catch{}}
  protocol.handle('duewise',req=>{
    const url=new URL(req.url);if(url.host!=='app') return new Response('',{status:403});
    const root=path.resolve(__dirname,'../dist');const target=path.resolve(root,'.'+decodeURIComponent(url.pathname==='/'?'/index.html':url.pathname));
    if(target!==root&&!target.startsWith(root+path.sep)) return new Response('',{status:403});
    return net.fetch(pathToFileURL(target).href);
  });
  session.defaultSession.setPermissionRequestHandler((_webContents,_permission,callback)=>callback(false));
  window=new BrowserWindow({width:1440,height:940,minWidth:1000,minHeight:720,backgroundColor:'#f6f7f9',icon:path.join(__dirname,'../build/icon.png'),title:'Duewise — Payment Workspace',webPreferences:{preload:path.join(__dirname,'preload.cjs'),nodeIntegration:false,contextIsolation:true,sandbox:true,webSecurity:true}});
  window.webContents.setWindowOpenHandler(()=>({action:'deny'}));
  window.webContents.on('will-navigate',(event)=>event.preventDefault());
  ipcMain.handle('duewise:request',(event,input)=>{validSender(event);return request(input);});
  ipcMain.handle('duewise:server-info',event=>{validSender(event);return base.origin;});
  ipcMain.handle('duewise:set-server',async(event,value)=>{validSender(event);base=validateOrigin(String(value));access=refresh='';await fs.writeFile(connectionFile,JSON.stringify({origin:base.origin}),'utf8');return base.origin;});
  ipcMain.handle('duewise:save-csv',async(event,{csv,name})=>{
    validSender(event);if(typeof csv!=='string'||csv.length>50000000||!/^duewise-\d{4}-\d{2}\.csv$/.test(name)) throw new Error('Invalid export');
    const result=await dialog.showSaveDialog(window,{defaultPath:name,filters:[{name:'CSV report',extensions:['csv']}]});
    if(result.canceled) return false;await fs.writeFile(result.filePath,csv,'utf8');return true;
  });
  if(process.env.DUEWISE_DEV==='1') window.loadURL('http://127.0.0.1:5173');else window.loadURL('duewise://app/');
});
app.on('window-all-closed',()=>app.quit());
