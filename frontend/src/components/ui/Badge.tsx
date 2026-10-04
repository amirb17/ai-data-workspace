import type { ReactNode } from "react"

type BadgeVariant =
  | "neutral"
  | "success"
  | "warning"
  | "error"
  | "processing"

type BadgeProps = {
  children: ReactNode
  variant?: BadgeVariant
}

const variants: Record<BadgeVariant, string> = {
  neutral:
    "bg-slate-100 text-slate-700",
  success:
    "bg-green-50 text-green-700",
  warning:
    "bg-amber-50 text-amber-700",
  error:
    "bg-red-50 text-red-700",
  processing:
    "bg-blue-50 text-blue-700",
}

export function Badge({
  children,
  variant = "neutral",
}: BadgeProps) {
  return (
    <span
      className={[
        "inline-flex items-center rounded-md px-2 py-1 text-xs font-medium",
        variants[variant],
      ].join(" ")}
    >
      {children}
    </span>
  )
}