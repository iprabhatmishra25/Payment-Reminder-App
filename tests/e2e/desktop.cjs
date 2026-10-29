/* End-to-end test against the real Electron main/preload/renderer and Python API.
 * Install backend deps and npm ci first. Linux needs a display (e.g. xvfb-run).
 * This always creates a fresh temporary demo database; it never uses production data.
 */
const {createRequire}=require('node:module');
const path=require('node:path');
const fs=require('node:fs');
const os=require('node:os');
const {spawn,spawnSync}=require('node:child_process');
const assert=require('node:assert/strict');
const root=path.resolve(__dirname,'../..');
const requireDesktop=createRequire(path.join(root,'apps/desktop/package.json'));
const {_electron}=requireDesktop('playwright');
const desktop=path.join(root,'apps/desktop'),backend=path.join(root,'services/backend');
const temp=fs.mkdtempSync(path.join(os.tmpdir(),'duewise-e2e-'));
const python=process.env.DUEWISE_TEST_PYTHON||path.join(root,'.venv',process.platform==='win32'?'Scripts/python.exe':'bin/python');
const env={...process.env,APP_ENV:'development',DEMO_MODE:'true',EMAIL_MODE:'preview',DATABASE_URL:'sqlite:///'+path.join(temp,'qa.db').replaceAll('\\','/'),ENCRYPTION_KEY:Buffer.alloc(32,7).toString('base64url')+'=',DUEWISE_API_URL:'http://127.0.0.1:8000',DUEWISE_DEV:process.env.DUEWISE_PACKAGED_BINARY?'0':'1'};
const processes=[];let electron,page;
function start(command,args,cwd,extra={}){const p=spawn(command,args,{cwd,env:{...env,...extra},stdio:['ignore','pipe','pipe']});p.stdout.on('data',()=>{});p.stderr.on('data',data=>{if(command.includes('Xvfb'))process.stderr.write(data);});processes.push(p);return p;}
async function ready(url){const end=Date.now()+30000;while(Date.now()<end){try{if((await fetch(url)).ok)return;}catch{}await new Promise(r=>setTimeout(r,200));}throw Error('Not ready: '+url);}
async function run(){
 for(const args of [['-m','alembic','upgrade','head'],['-m','app.cli','seed-demo']]){const r=spawnSync(python,args,{cwd:backend,env,encoding:'utf8'});if(r.status!==0)throw Error(r.stderr||r.stdout);}
 if(process.env.DUEWISE_XVFB){const display=200+Math.floor(Math.random()*1000);start(process.env.DUEWISE_XVFB,[':'+display,'-ac','-nolisten','unix','-nolisten','local','-listen','tcp','-screen','0','1440x1000x24'],root);env.DISPLAY='127.0.0.1:'+display;await new Promise(r=>setTimeout(r,1500));}
 start(python,['-m','uvicorn','app.main:app','--host','127.0.0.1','--port','8000','--no-proxy-headers'],backend);
 if(!process.env.DUEWISE_PACKAGED_BINARY)start(process.execPath,[path.join(desktop,'node_modules/vite/bin/vite.js'),'--host','127.0.0.1'],desktop);
 await ready('http://127.0.0.1:8000/health/live');
 if(!process.env.DUEWISE_PACKAGED_BINARY)await ready('http://127.0.0.1:5173');
 electron=await _electron.launch({executablePath:process.env.DUEWISE_PACKAGED_BINARY||requireDesktop('electron'),args:[...(process.getuid?.()===0?['--no-sandbox']:[]),'--disable-gpu',...(process.env.DUEWISE_PACKAGED_BINARY?[]:[desktop])],env,timeout:30000});
 page=await electron.firstWindow();await page.waitForLoadState('domcontentloaded');
 const errors=[];page.on('pageerror',error=>errors.push(error.message));
 // Enter credentials as a user would; do not race the optional async demo autofill.
 await page.getByLabel('Work email',{exact:true}).fill('demo@duewise.example.com');
 await page.getByLabel('Password',{exact:true}).fill('DemoOnly!2026');
 await page.getByRole('button',{name:'Sign in to workspace'}).click();
 await Promise.race([
   page.getByRole('heading',{name:'Overview.'}).waitFor(),
   page.getByRole('alert').waitFor().then(async()=>{throw Error('Sign-in failed: '+await page.getByRole('alert').innerText());})
 ]);
 await page.getByText('Payments collected',{exact:true}).waitFor();
 await page.getByText('Northstar Technologies',{exact:true}).waitFor();
 await page.locator('.recharts-pie-sector path').first().waitFor();
 assert.equal(await page.locator('.recharts-pie-sector path').count(),2);
 console.log('PASS login, dashboard, rendered pie sectors and company report');
 if(process.env.QA_SCREENSHOT_DIR){fs.mkdirSync(process.env.QA_SCREENSHOT_DIR,{recursive:true});await page.screenshot({path:path.join(process.env.QA_SCREENSHOT_DIR,'duewise-overview.png'),fullPage:true});}
 await page.locator('nav').getByRole('button',{name:'Companies',exact:true}).click();
 await page.getByRole('heading',{name:'Your companies'}).waitFor();
 await page.getByRole('button',{name:'Add company',exact:true}).click();
 await page.getByLabel('Company name',{exact:true}).fill('QA Test Company');
 await page.getByLabel('Company code',{exact:true}).fill('QA-001');
 await page.getByLabel('Billing email',{exact:true}).fill('qa@example.com');
 await page.getByRole('dialog').getByRole('button',{name:'Add company',exact:true}).click();
 await page.getByText('QA Test Company',{exact:true}).waitFor();
 console.log('PASS company creation through real IPC and API');
 await page.locator('nav').getByRole('button',{name:'Invoices',exact:true}).click();
 await page.getByRole('heading',{name:'Invoice register'}).waitFor();
 await page.getByRole('button',{name:'New invoice',exact:true}).click();
 await page.getByRole('dialog').getByLabel('Search company').fill('QA Test');
 await page.getByRole('dialog').getByRole('button',{name:/QA Test Company/}).click();
 await page.getByLabel('Invoice number',{exact:true}).fill('QA-INVOICE-001');
 await page.getByLabel('Due date',{exact:true}).fill(new Date(Date.now()+86400000).toISOString().slice(0,10));
 await page.getByLabel('Amount (INR)',{exact:true}).fill('1000.00');
 await page.getByRole('button',{name:'Save invoice',exact:true}).click();
 await page.getByText('QA-INVOICE-001',{exact:true}).waitFor();
 const invoiceRow=page.getByRole('row').filter({hasText:'QA-INVOICE-001'});
 await invoiceRow.getByRole('button',{name:'Record payment'}).click();
 await page.getByLabel('Verified amount (INR)',{exact:true}).fill('400.00');
 await page.getByRole('dialog').getByRole('button',{name:'Record payment',exact:true}).click();
 await page.getByRole('dialog').waitFor({state:'hidden'});
 await invoiceRow.getByText('partially paid',{exact:true}).waitFor();
 assert((await invoiceRow.innerText()).includes('600'));
 console.log('PASS invoice creation, partial payment, refreshed balance');
 await page.locator('nav').getByRole('button',{name:'Reminders',exact:true}).click();
 await page.getByRole('button',{name:'Preview reminders',exact:true}).click();
 await page.getByRole('dialog',{name:'Review your reminders'}).waitFor();
 await page.getByRole('button',{name:'Queue reminders',exact:true}).click();
 await page.getByRole('dialog').waitFor({state:'hidden'});
 await page.getByText('queued',{exact:true}).first().waitFor();
 console.log('PASS reminder preview and durable queue');
 await page.locator('.sidebar-bottom').getByRole('button',{name:'Settings',exact:true}).click();
 await page.getByRole('heading',{name:'Monthly reminder schedule'}).waitFor();
 await page.getByLabel('Day of month',{exact:true}).fill('7');
 await page.getByRole('button',{name:'Save schedule',exact:true}).click();
 await page.getByText('Reminder schedule saved',{exact:true}).waitFor();
 console.log('PASS settings and server-side policy persistence');
 await page.getByRole('button',{name:'Sign out',exact:true}).click();
 await page.getByRole('heading',{name:'Welcome back'}).waitFor();
 assert.deepEqual(errors,[],'Renderer errors: '+errors.join('; '));
 console.log('PASS logout; no uncaught renderer errors');
}
run().catch(async error=>{console.error(error);if(page)console.error('Visible form errors:',await page.locator('.form-error').allTextContents().catch(()=>[]));process.exitCode=1;}).finally(async()=>{if(electron)await electron.close();await Promise.all(processes.reverse().map(p=>new Promise(resolve=>{if(p.exitCode!==null)return resolve();p.once('exit',resolve);p.kill('SIGTERM');const timer=setTimeout(()=>{p.kill('SIGKILL');resolve();},5000);timer.unref();})));fs.rmSync(temp,{recursive:true,force:true,maxRetries:10,retryDelay:200});});
