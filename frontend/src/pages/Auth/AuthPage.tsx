import {useEffect, useRef, useState, type FormEvent} from 'react'
import {Link, useLocation, useNavigate, useSearchParams} from 'react-router-dom'
import {Database} from 'lucide-react'
import {authConfig, authPost, googleUrl, safeReturn, type Challenge} from '../../services/api/auth'
import {getCurrentUser} from '../../services/api/identity'
import {announceAuthChange} from '../../services/api/client'
import {primary} from '../Public/PublicPage'
import {PasswordField,OtpFields} from '../../features/auth/AuthFields'
import {authField as field} from '../../features/auth/styles'



export function AuthPage({mode}: {mode: string}) {
 const navigate=useNavigate(), location=useLocation(), [params]=useSearchParams()
 const returnTo=safeReturn(params.get('return_to'))
 const [google,setGoogle]=useState(false), [busy,setBusy]=useState(false), [error,setError]=useState(params.get('error')==='google'?'Google sign-in did not finish. Please try again.':''), [success,setSuccess]=useState('')
 const [challenge,setChallenge]=useState<Challenge | undefined>((location.state as {challenge?: Challenge}|null)?.challenge)
 const [resendAt,setResendAt]=useState(()=>Date.now()+(challenge?.resend_after??0)*1000)
 const [remaining,setRemaining]=useState(challenge?.resend_after??0)
 const [code,setCode]=useState('      '), [resetToken,setResetToken]=useState<string>()
 const inputs=useRef<(HTMLInputElement|null)[]>([])
 const newPassword=useRef<HTMLInputElement>(null)
 useEffect(()=>{if(resetToken)newPassword.current?.focus()},[resetToken])
 const signup=mode==='signup', verify=mode==='verify', reset=mode==='reset-password'
 const purpose=(location.state as {purpose?:string}|null)?.purpose || 'VERIFY_EMAIL'
 useEffect(()=>{authConfig().then(value=>setGoogle(value.google_available)).catch(()=>setError('Sign-in is temporarily unavailable. Please retry.')); if(mode==='login'||mode==='signup')getCurrentUser().then(()=>navigate(returnTo,{replace:true})).catch(()=>{})},[mode,navigate,returnTo])
 useEffect(()=>{const tick=()=>setRemaining(Math.max(0,Math.ceil((resendAt-Date.now())/1000)));const timer=window.setInterval(tick,1000);window.addEventListener('focus',tick);return()=>{window.clearInterval(timer);window.removeEventListener('focus',tick)}},[resendAt])
 async function submit(event: FormEvent<HTMLFormElement>){
   event.preventDefault();setBusy(true);setError('')
   const data=Object.fromEntries(new FormData(event.currentTarget))
   try{
     if(mode==='login'){await authPost('login',{email:data.email,password:data.current_password});announceAuthChange();navigate(returnTo,{replace:true})}
     else if(signup||mode==='forgot-password'){
       const result=await authPost<Challenge>(signup?'signup':'forgot-password',data)
       navigate('/verify',{state:{challenge:result,purpose:signup?'VERIFY_EMAIL':'RESET_PASSWORD'}})
     }else if(resetToken){await authPost('reset-password',{...data,reset_token:resetToken});setResetToken(undefined);setSuccess('Password updated. Sign in with your new password.')}else if(verify&&challenge){
       const result=await authPost<{reset_token?:string}>('verify',{challenge_id:challenge.challenge_id,code,purpose})
       if(purpose==='RESET_PASSWORD'){setResetToken(result.reset_token);setSuccess('Code verified. Choose your new password.')}
       else setSuccess('Email verified. Your account is ready.')
     }
   }catch(reason){setError(reason instanceof Error?reason.message:'Please try again.')}finally{setBusy(false)}
 }
 const title=resetToken?'Choose a new password':success?'You’re all set':verify?'Check your email':signup?'Create your account':mode==='forgot-password'?'Forgot password?':reset?'Reset password':'Welcome back'
 return <div className="grid min-h-screen bg-white lg:grid-cols-2"><aside className="hidden flex-col justify-between bg-slate-950 p-14 text-white lg:flex"><Link to="/" className="flex items-center gap-3 text-2xl font-semibold"><Database/>DataRise AI</Link><div><h2 className="max-w-md text-4xl font-semibold leading-tight">Your data, prepared for better decisions.</h2><p className="mt-6 max-w-md leading-relaxed text-slate-300">Prepare datasets, connect what belongs together and explore trusted analytics in one workspace.</p></div><p className="text-sm text-slate-400">Trusted data first. Clear answers next.</p></aside><main className="flex min-w-0 items-center justify-center px-5 py-10"><div className="w-full max-w-md"><Link to="/" className="mb-10 inline-flex items-center gap-2 font-semibold text-indigo-700 lg:hidden"><Database size={20}/>DataRise AI</Link><h1 className="text-3xl font-semibold tracking-tight">{title}</h1><p className="mb-7 mt-3 text-slate-600">{success&&!resetToken?'Continue to sign in.':verify&&challenge?<>If an eligible account exists for <strong>{challenge.masked_email}</strong>, we’ve sent a 6-digit verification code.</>:signup?'Start with a workspace that belongs to you.':mode==='forgot-password'?'Enter your email to receive a verification code.':'Continue to your DataRise workspace.'}</p>
 {error&&<p role="alert" className="mb-5 rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-800">{error}</p>}
 {success&&!resetToken?<div role="status"><p className="mb-6 rounded-lg bg-emerald-50 p-4 text-emerald-800">{success}</p><Link className={primary} to="/login">Sign in</Link></div>:((verify&&!challenge)||reset&&!resetToken)?<p>Request a new code to continue. <Link to="/forgot-password" className="text-indigo-700 underline">Send code</Link></p>:<>
 <form onSubmit={submit} className="space-y-5">
 {resetToken?<><p role="status" className="text-sm text-emerald-700">{success}</p><PasswordField label="New password" inputRef={newPassword}/><PasswordField label="Confirm password" name="confirm_password" confirm/></>:verify?<><OtpFields value={code} onChange={setCode} inputs={inputs}/></>:<>
 {signup&&<label className="block text-sm font-medium">Full name<input className={field} name="display_name" autoComplete="name" maxLength={100} required/></label>}
 <label className="block text-sm font-medium">Email<input className={field} name="email" type="email" autoComplete="email" maxLength={320} required/></label>
 {(signup||mode==='login')&&<PasswordField name={mode==='login'?'current_password':'password'}/>}
 {signup&&<><p className="text-xs text-slate-500">Use 12–128 characters. No special character rules.</p><PasswordField label="Confirm password" name="confirm_password" confirm/></>}
 </>}
 <button className={primary+' w-full'} disabled={busy||verify&&!resetToken&&!/^\d{6}$/.test(code)}>{busy?'Please wait…':resetToken?'Reset password':verify?'Verify code':signup?'Create account':mode==='forgot-password'?'Send code':'Sign in'}</button>
 </form>
 {verify&&!resetToken&&<button disabled={busy||remaining>0} className="mt-5 min-h-11 w-full text-sm font-medium text-indigo-700 disabled:text-slate-400" onClick={async()=>{setBusy(true);setError('');try{const next=await authPost<Challenge>('resend',{challenge_id:challenge?.challenge_id});setChallenge(next);setRemaining(next.resend_after);setResendAt(Date.now()+next.resend_after*1000);setCode('      ');inputs.current[0]?.focus()}catch(reason){setError(reason instanceof Error?reason.message:'Please retry.')}finally{setBusy(false)}}}>{remaining?`Resend code in ${remaining}s`:'Resend code'}</button>}
 {(signup||mode==='login')&&<><div className="my-6 flex items-center gap-3 text-xs text-slate-400"><hr className="flex-1 border-slate-200"/>or<hr className="flex-1 border-slate-200"/></div>{google?<a href={googleUrl(returnTo)} className="flex min-h-11 w-full items-center justify-center rounded-lg border border-slate-300 p-3 font-medium">Continue with Google</a>:<div><button type="button" disabled aria-describedby="google-unavailable" className="min-h-11 w-full rounded-lg border border-slate-200 p-3 font-medium text-slate-400">Continue with Google</button><p id="google-unavailable" className="mt-2 text-center text-xs text-slate-500">Google sign-in is not available yet.</p></div>}<p className="mt-6 text-center text-sm text-slate-600">{signup?'Already have an account?':'New to DataRise?'} <Link to={signup?'/login':'/signup'} className="font-medium text-indigo-700">{signup?'Sign in':'Create account'}</Link></p>{!signup&&<Link to="/forgot-password" className="mt-4 block text-center text-sm font-medium text-indigo-700">Forgot password?</Link>}</>}
 </>}
 <Link to="/" className="mt-8 block text-center text-sm text-slate-500">Back to DataRise</Link></div></main></div>
}
