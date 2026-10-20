const assert=require('node:assert/strict'),fs=require('node:fs'),ts=require('typescript')
async function main(){
 const {Window}=await import('happy-dom'),window=new Window({url:'http://localhost:5173'})
 const breakpoints=[]
 window.matchMedia=query=>{const listeners=new Set(),media={media:query,matches:false,addEventListener:(name,listener)=>listeners.add(listener),removeEventListener:(name,listener)=>listeners.delete(listener),resize:()=>{media.matches=true;for(const listener of listeners)listener()}};breakpoints.push(media);return media}
 for(const name of ['window','document','HTMLElement','HTMLInputElement','Element','Node','Event','MouseEvent','KeyboardEvent','navigator','FormData','localStorage'])Object.defineProperty(global,name,{value:name==='window'?window:window[name],configurable:true})
 global.IS_REACT_ACT_ENVIRONMENT=true
 for(const ext of ['.ts','.tsx'])require.extensions[ext]=(module,file)=>module._compile(ts.transpileModule(fs.readFileSync(file,'utf8').replaceAll('import.meta.env','({VITE_API_BASE_URL:"http://localhost:8000"})'),{compilerOptions:{module:ts.ModuleKind.CommonJS,jsx:ts.JsxEmit.ReactJSX}}).outputText,file)
 const React=require('react'),{act}=React,{createRoot}=require('react-dom/client'),{MemoryRouter,Routes,Route,useLocation}=require('react-router-dom')
 const {AuthPage}=require('../src/pages/Auth/AuthPage.tsx'),{AppShell}=require('../src/components/layout/AppShell.tsx'),{IdentityContext}=require('../src/services/identityContext.ts'),{IdentityBootstrap}=require('../src/components/layout/IdentityBootstrap.tsx'),{safeReturn}=require('../src/services/api/auth.ts'),{PublicPage}=require('../src/pages/Public/PublicPage.tsx')
 for(const bad of ['https://evil.example','//evil.example','/app/../login','/app/%2e%2e/x','/app/\\evil','/app/%0aevil'])assert.equal(safeReturn(bad),'/app')
 let requests=[],signedIn=false
 global.fetch=async(url,options={})=>{
  requests.push({url,options});let value,status=200
  if(url.endsWith('/auth/config'))value={google_available:true,identity_mode:'session'}
  else if(url.endsWith('/auth/csrf'))value={csrf_token:'synthetic-signed-token'}
  else if(url.endsWith('/me')){status=signedIn?200:401;value=signedIn?{user_id:1,display_name:'Alice',identity_mode:'session',email:'alice@example.com'}:{detail:'Sign in to continue.'}}
  else if(url.endsWith('/auth/login')){signedIn=true;value={status:'SIGNED_IN'}}
  else if(url.endsWith('/auth/verify'))value={reset_token:'fixture-reset-token'}
  else if(url.endsWith('/auth/reset-password'))value={status:'PASSWORD_UPDATED'}
  else value={status:'SIGNED_OUT'}
  return {ok:status===200,status,json:async()=>value}
 }
 const host=document.createElement('div');document.body.append(host);const root=createRoot(host)
 const action=async fn=>act(async()=>{fn();await new Promise(resolve=>setTimeout(resolve,10))})
 const click=element=>element.dispatchEvent(new window.MouseEvent('click',{bubbles:true}))
 const fill=(input,value)=>{Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype,'value').set.call(input,value);input.dispatchEvent(new window.Event('input',{bubbles:true}))}
 const submit=()=>host.querySelector('form').dispatchEvent(new window.Event('submit',{bubbles:true,cancelable:true}))
 function Location(){return React.createElement('p',{'data-location':true},useLocation().pathname)}
 await action(()=>root.render(React.createElement(MemoryRouter,{initialEntries:['/login?return_to=%2Fapp%2Fworkspaces']},React.createElement(Routes,null,React.createElement(Route,{path:'/login',element:React.createElement(AuthPage,{mode:'login'})}),React.createElement(Route,{path:'*',element:React.createElement(Location)})))))
 await action(()=>{fill(host.querySelector('[name=email]'),'alice@example.com');fill(host.querySelector('[name=current_password]'),'correct horse battery')})
 await action(submit)
 assert.equal(host.querySelector('[data-location]').textContent,'/app/workspaces')
 const login=requests.find(call=>call.url.endsWith('/auth/login'))
 assert.deepEqual(JSON.parse(login.options.body),{email:'alice@example.com',password:'correct horse battery'})
 assert.equal(login.options.credentials,'include');assert.equal(login.options.headers['X-CSRF-Token'],'synthetic-signed-token')
 await act(async()=>root.unmount())
 const second=createRoot(host),challenge={challenge_id:'fixture',masked_email:'a***@example.com',resend_after:60}
 await action(()=>second.render(React.createElement(MemoryRouter,{initialEntries:[{pathname:'/verify',state:{challenge,purpose:'RESET_PASSWORD'}}]},React.createElement(AuthPage,{mode:'verify'}))))
 const pasted=new window.Event('paste',{bubbles:true,cancelable:true});pasted.clipboardData={getData:()=> '123456'}
 await action(()=>host.querySelector('input').dispatchEvent(pasted));await action(submit)
 assert.ok(host.textContent.includes('Choose a new password'))
 assert.equal(document.activeElement,host.querySelector('[name=password]'),'verified reset moves focus to the new password')
 await action(()=>{fill(host.querySelector('[name=password]'),'another safe password');fill(host.querySelector('[name=confirm_password]'),'another safe password')});await action(submit)
 assert.ok(host.textContent.includes('Password updated'))
 assert.equal(requests.filter(call=>call.url.endsWith('/auth/verify')).length,1,'reset submits the grant, not the OTP twice')
 assert.equal(JSON.parse(requests.find(call=>call.url.endsWith('/auth/reset-password')).options.body).reset_token,'fixture-reset-token')
 await act(async()=>second.unmount())
 signedIn=false
 const third=createRoot(host)
 await action(()=>third.render(React.createElement(MemoryRouter,{initialEntries:['/app']},React.createElement(Routes,null,React.createElement(Route,{path:'/app',element:React.createElement(IdentityBootstrap,null,React.createElement('p',null,'PRIVATE CONTENT'))}),React.createElement(Route,{path:'/login',element:React.createElement(Location)})))))
 assert.ok(!host.textContent.includes('PRIVATE CONTENT'));assert.equal(host.querySelector('[data-location]').textContent,'/login')
 await act(async()=>third.unmount())
 const fourth=createRoot(host)
 await action(()=>fourth.render(React.createElement(IdentityContext.Provider,{value:{userId:1,displayName:'Alice'}},React.createElement(MemoryRouter,null,React.createElement(AppShell,null,'Workspace content')))))
 await action(()=>click(host.querySelector('[aria-label="Collapse sidebar"]')))
 assert.equal(localStorage.getItem('datarise-sidebar-collapsed'),'true');assert.ok(host.querySelector('[aria-label="Expand sidebar"]'))
 await action(()=>click(host.querySelector('[aria-label="Open navigation"]')));assert.ok(host.querySelector('[role=dialog]'))
 await action(()=>document.dispatchEvent(new window.KeyboardEvent('keydown',{key:'Escape',bubbles:true})));assert.ok(!host.querySelector('[role=dialog]'))
 await action(()=>click(host.querySelector('[aria-label="Open navigation"]')))
 await action(()=>breakpoints.forEach(media=>media.resize()))
 assert.ok(!host.querySelector('[role=dialog]'),'crossing to desktop removes the mobile focus trap')
 await act(async()=>fourth.unmount())
 const fifth=createRoot(host)
 await action(()=>fifth.render(React.createElement(MemoryRouter,null,React.createElement(PublicPage,{kind:'home'}))))
 assert.ok(host.textContent.includes('Turn raw data into trusted insight.'))
 await action(()=>click(host.querySelector('[aria-label="Open navigation"]')));assert.ok(host.querySelector('[role=dialog]'))
 await action(()=>document.dispatchEvent(new window.KeyboardEvent('keydown',{key:'Escape',bubbles:true})));assert.ok(!host.querySelector('[role=dialog]'))
 await act(async()=>fifth.unmount());await window.happyDOM.close()
 console.log('Auth interactions passed: cookies/CSRF, login return, OTP paste/reset, protected redirect, sidebar preference and menus')
}
main().catch(error=>{console.error(error);process.exitCode=1})
