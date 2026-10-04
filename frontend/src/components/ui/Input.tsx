import type { InputHTMLAttributes } from "react"

type InputProps = InputHTMLAttributes<HTMLInputElement>

export function Input({
  className = "",
  ...props
}: InputProps) {
  return (
    <input
      className={[
        "h-10 w-full rounded-lg border border-slate-200 bg-white px-3 text-sm text-slate-900 outline-none transition",
        "placeholder:text-slate-400",
        "focus:border-indigo-500 focus:ring-2 focus:ring-indigo-100",
        className,
      ].join(" ")}
      {...props}
    />
  )
}