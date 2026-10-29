const {contextBridge,ipcRenderer}=require('electron');
contextBridge.exposeInMainWorld('duewise',{
  request:request=>ipcRenderer.invoke('duewise:request',request),
  saveCsv:(csv,name)=>ipcRenderer.invoke('duewise:save-csv',{csv,name}),
  serverInfo:()=>ipcRenderer.invoke('duewise:server-info'),
  setServer:origin=>ipcRenderer.invoke('duewise:set-server',origin)
});
