import {useId, useState, type Dispatch, type SetStateAction, type RefObject, type Ref} from 'react'
import {Eye, EyeOff} from 'lucide-react'
import {authField as field} from './styles'

export function PasswordField({label='Password',name='password',confirm=false,inputRef}: {label?: string;name?: string;confirm?: boolean;inputRef?:Ref<HTMLInputElement>}) {
 const [visible,setVisible]=useState(false)
 const id=useId()
 return <div className="block text-sm font-medium">
  <label htmlFor={id}>{label}</label>
  <span className="relative block">
   <input id={id} ref={inputRef} required name={name} type={visible?'text':'password'}
    minLength={name==='current_password'?1:12} maxLength={128}
    autoComplete={confirm?'new-password':name==='current_password'?'current-password':'new-password'}
    className={field+' pr-12'}/>
   <button type="button" onClick={()=>setVisible(!visible)}
    aria-label={(visible?'Hide ':'Show ')+label.toLowerCase()} aria-pressed={visible}
    className="absolute right-1 top-3 p-3 text-slate-500">
    {visible?<EyeOff size={18}/>:<Eye size={18}/>}</button>
  </span>
 </div>
}

export function OtpFields({value,onChange,inputs}: {
 value:string;
 onChange:Dispatch<SetStateAction<string>>;
 inputs:RefObject<(HTMLInputElement|null)[]>;
}) {
 return <div className="flex gap-2" role="group" aria-label="Six-digit verification code">
  {Array.from({length:6},(_,index)=><input
   key={index}
   ref={element=>{inputs.current[index]=element}}
   aria-label={'Digit '+(index+1)}
   inputMode="numeric"
   autoComplete={index===0?'one-time-code':'off'}
   value={value[index]?.trim()||''}
   pattern="[0-9]" maxLength={1} required
   className="h-14 min-w-0 w-full rounded-lg border border-slate-300 text-center text-2xl focus:ring-2 focus:ring-indigo-500"
   onPaste={event=>{
    event.preventDefault()
    const pasted=event.clipboardData.getData('text').replace(/\D/g,'').slice(0,6)
    onChange(pasted.padEnd(6))
    inputs.current[Math.min(pasted.length,5)]?.focus()
   }}
   onChange={event=>{
    const digit=event.target.value.replace(/\D/g,'').slice(-1)
    onChange(previous=>previous.slice(0,index)+(digit||' ')+previous.slice(index+1))
    if(digit)inputs.current[index+1]?.focus()
   }}
   onKeyDown={event=>{
    if(event.key==='Backspace'&&!value[index]?.trim())inputs.current[index-1]?.focus()
    if(event.key==='ArrowLeft')inputs.current[index-1]?.focus()
    if(event.key==='ArrowRight')inputs.current[index+1]?.focus()
   }}
  />)}
 </div>
}
