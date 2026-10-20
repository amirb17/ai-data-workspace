import {useState, type FormEvent} from 'react'
import {useNavigate} from 'react-router-dom'
import {useCurrentUser} from '../../services/identityContext'
import {request} from '../../services/api/client'
import {authConfig, authPost, googleUrl, signOut} from '../../services/api/auth'
import {useApiResource} from '../../services/api/useApiResource'
import {PasswordField} from '../../features/auth/AuthFields'
import {primary} from '../Public/PublicPage'
export function SettingsPage(){
 const user=useCurrentUser(),navigate=useNavigate()
 const configuration=useApiResource('auth-configuration',authConfig)
 const [error,setError]=useState(''),[busy,setBusy]=useState(false)
 async function save(event:FormEvent<HTMLFormElement>,kind:'profile'|'password'){
   event.preventDefault();setError('');setBusy(true)
   const data=Object.fromEntries(new FormData(event.currentTarget))
   try{if(kind==='profile'){await request('/auth/profile',{method:'PATCH',body:JSON.stringify(data)});window.location.reload()}
   else{await authPost('change-password',data);await signOut();navigate('/login',{replace:true})}}
   catch(reason){setError(reason instanceof Error?reason.message:'Please retry.')}finally{setBusy(false)}
 }
 return <div className="max-w-2xl space-y-7"><header><h1 className="text-3xl font-semibold">Settings</h1><p className="mt-2 text-slate-600">Your profile and sign-in preferences.</p></header>{error&&<p role="alert" className="rounded-lg bg-red-50 p-4 text-red-800">{error}</p>}
 {user.identityMode==='development'?<p className="rounded-xl border border-slate-200 bg-white p-6 text-slate-600">This local environment uses a development identity. Account settings become available when session authentication is configured.</p>:<>
 <section className="rounded-xl border border-slate-200 bg-white p-6"><h2 className="text-lg font-semibold">Profile</h2><div className="mt-4 flex items-center gap-4">{user.avatarUrl?<img src={user.avatarUrl} referrerPolicy="no-referrer" alt="Your avatar" className="h-12 w-12 rounded-full"/>:<span className="flex h-12 w-12 items-center justify-center rounded-full bg-indigo-100 font-semibold text-indigo-700">{user.displayName.slice(0,1).toUpperCase()}</span>}<div><p>{user.email}</p><p className="text-sm text-slate-500">Verified email</p></div></div><form onSubmit={event=>save(event,'profile')} className="mt-5 space-y-4"><label className="block text-sm font-medium">Full name<input name="display_name" defaultValue={user.displayName} required maxLength={100} className="mt-2 block min-h-11 w-full rounded-lg border border-slate-300 px-3"/></label><button className={primary} disabled={busy}>Save profile</button></form></section>
 {user.hasPassword&&<section className="rounded-xl border border-slate-200 bg-white p-6"><h2 className="text-lg font-semibold">Security</h2><p className="mt-2 text-sm text-slate-500">Changing your password signs you out on all devices.</p><form onSubmit={event=>save(event,'password')} className="mt-5 space-y-4"><PasswordField name="current_password" label="Current password"/><PasswordField label="New password"/><PasswordField name="confirm_password" label="Confirm password" confirm/><button className={primary} disabled={busy}>Change password</button></form></section>}
 <section className="rounded-xl border border-slate-200 bg-white p-6"><h2 className="text-lg font-semibold">Connected sign-in</h2><p className="mt-3 text-sm text-slate-600">{user.loginMethods?.includes('google')?'Google is connected.':'Google is not connected.'} {user.hasPassword?'Email and password are available.':''}</p>{!user.loginMethods?.includes('google')&&configuration.data?.google_available&&<a href={googleUrl('/app/settings',true)} className="mt-4 inline-flex min-h-11 items-center rounded-lg border border-slate-300 px-4 text-sm">Connect Google</a>}</section>
 <button className="min-h-11 rounded-lg border border-slate-300 bg-white px-5 text-sm font-medium" disabled={busy} onClick={async()=>{setBusy(true);try{await signOut();navigate('/login',{replace:true})}catch{setError('Unable to sign out. Please retry.')}finally{setBusy(false)}}}>Sign out</button></>}
 </div>
}
