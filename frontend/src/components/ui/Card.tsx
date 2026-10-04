import type { HTMLAttributes } from "react"

type CardProps = HTMLAttributes<HTMLDivElement>

export function Card({
  className = "",
  children,
  ...props
}: CardProps) {
  return (
    <div
      className={[
        "rounded-xl border border-slate-200 bg-white shadow-sm",
        className,
      ].join(" ")}
      {...props}
    >
      {children}
    </div>
  )
}