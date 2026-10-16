import { Link } from "react-router-dom"

export type SectionLink = { label: string; path: string; sections?: string[] }

export function SectionNavigation({ label, base, current, links, secondary = false }: { label: string; base: string; current: string; links: SectionLink[]; secondary?: boolean }) {
  return <nav aria-label={label} className={`flex flex-wrap gap-x-4 gap-y-1 ${secondary ? "text-sm" : "border-b border-slate-200"}`}>
    {links.map(link => {
      const selected = (link.sections ?? [link.path]).includes(current)
      return <Link key={link.path} to={`${base}${link.path ? `/${link.path}` : ""}`} aria-current={selected ? "page" : undefined}
        className={`inline-flex min-h-11 items-center px-1 text-sm font-medium transition ${secondary ? selected ? "text-indigo-700 underline underline-offset-4" : "text-slate-500 hover:text-slate-900" : `border-b-2 ${selected ? "border-indigo-600 text-indigo-700" : "border-transparent text-slate-500 hover:text-slate-900"}`}`}>
        {link.label}
      </Link>
    })}
  </nav>
}
