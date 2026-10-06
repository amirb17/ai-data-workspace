import { useCurrentUser } from "../../services/identityContext"
import logo from "../../assets/datarise-ai-logo.png"
import {
  NavLink,
} from "react-router-dom"
import {
  BarChart3,
  Home,
  Menu,
  Settings,
  ShieldCheck,
  Workflow,
  LayoutGrid,
} from "lucide-react"
import { useState } from "react"

const navItems = [
  {
    label: "Home",
    icon: Home,
    to: "/app",
  },
  {
    label: "Workspaces",
    icon: LayoutGrid,
    to: "/app/workspaces",
  },
  {
    label: "Processing",
    icon: Workflow,
    to: "/app/processing",
  },
  {
    label: "Analytics",
    icon: BarChart3,
    to: "/app/analytics",
  },
  {
    label: "Data Quality",
    icon: ShieldCheck,
    to: "/app/data-quality",
  },
]

type AppShellProps = {
  children: React.ReactNode
}

export function AppShell({
  children,
}: AppShellProps) {
  const user = useCurrentUser()
  const initials = user.displayName.trim().split(/\s+/).slice(0, 2).map((part) => part[0]).join("").toUpperCase() || "U"
  const [mobileOpen, setMobileOpen] = useState(false)

  return (
    <div className="min-h-screen bg-slate-50">
      {/* Desktop sidebar */}
      <aside className="fixed inset-y-0 left-0 hidden w-64 border-r border-slate-800/80 bg-[#050B18] lg:flex lg:flex-col">
        {/* Brand */}
        <div className="flex h-20 items-center border-b border-slate-800/80 px-5">
            <img
            src={logo}
            alt="DataRise AI"
            className="h-10 w-auto object-contain"
            />
        </div>

        {/* Navigation */}
        <div className="flex-1 px-3 py-6">
            <p className="mb-3 px-3 text-[11px] font-semibold uppercase tracking-[0.16em] text-slate-500">
            Workspace
            </p>

            <nav className="space-y-1.5">
            {navItems.map((item) => {
                const Icon = item.icon

                return (
                <NavLink
                    key={item.label}
                    to={item.to}
                    end={item.to === "/app"}
                    className={({ isActive }) =>
                    [
                        "group relative flex min-h-11 w-full items-center gap-3 rounded-lg px-3 py-2.5 text-sm font-medium transition-all duration-200",
                        isActive
                        ? "bg-white/[0.08] text-white"
                        : "text-slate-300 hover:bg-white/[0.05] hover:text-white",
                    ].join(" ")
                    }
                >
                    {({ isActive }) => (
                    <>
                        {isActive && (
                        <span className="absolute bottom-2 left-0 top-2 w-0.5 rounded-full bg-amber-400" />
                        )}

                        <Icon
                        size={19}
                        className={
                            isActive
                            ? "text-amber-400"
                            : "text-slate-400 transition group-hover:text-slate-200"
                        }
                        />

                        <span>
                        {item.label}
                        </span>
                    </>
                    )}
                </NavLink>
                )
            })}
            </nav>
        </div>

        {/* Footer */}
        <div className="border-t border-slate-800/80 p-3">
            <NavLink
            to="/app/settings"
            className={({ isActive }) =>
                [
                "group relative flex min-h-11 w-full items-center gap-3 rounded-lg px-3 py-2.5 text-sm font-medium transition-all duration-200",
                isActive
                    ? "bg-white/[0.08] text-white"
                    : "text-slate-300 hover:bg-white/[0.05] hover:text-white",
                ].join(" ")
            }
            >
            {({ isActive }) => (
                <>
                {isActive && (
                    <span className="absolute bottom-2 left-0 top-2 w-0.5 rounded-full bg-amber-400" />
                )}

                <Settings
                    size={19}
                    className={
                    isActive
                        ? "text-amber-400"
                        : "text-slate-400 group-hover:text-slate-200"
                    }
                />

                Settings
                </>
            )}
            </NavLink>
        </div>
        </aside>
      {/* Mobile topbar */}
      <header className="sticky top-0 z-30 flex h-16 items-center justify-between border-b border-slate-800 bg-slate-950 px-4 lg:hidden">
        <div className="flex items-center">
            <img
                src={logo}
                alt="DataRise AI"
                className="h-8 w-auto object-contain"
            />
        </div>

        <button
            onClick={() => setMobileOpen(true)}
            className="flex h-10 w-10 items-center justify-center rounded-lg border border-slate-700 bg-slate-900 text-white transition hover:bg-slate-800"
            aria-label="Open navigation"
            >
            <Menu size={20} />
        </button>
      </header>

      {/* Mobile drawer */}
      {mobileOpen && (
        <div className="fixed inset-0 z-50 lg:hidden">
          <button
            className="absolute inset-0 bg-slate-950/40"
            onClick={() => setMobileOpen(false)}
            aria-label="Close navigation"
          />

          <aside className="relative h-full w-72 overflow-y-auto bg-slate-950 p-4 shadow-xl">
            <div className="mb-8 flex items-center justify-between">
              <div>
                <div className="flex items-center">
                <img
                    src={logo}
                    alt="DataRise AI"
                    className="h-8 w-auto object-contain"
                />
                </div>
                <p className="text-xs text-slate-500">
                    AI Data Workspace
                </p>
            </div>

              <button
                onClick={() => setMobileOpen(false)}
                className="rounded-lg px-3 py-2 text-sm text-slate-300 hover:bg-slate-900"
              >
                Close
              </button>
            </div>

            <nav className="space-y-1">
              {navItems.map((item) => {
                    const Icon = item.icon

                    return (
                        <NavLink
                        key={item.label}
                        to={item.to}
                        end={item.to === "/app"}
                        onClick={() => setMobileOpen(false)}
                        className={({ isActive }) =>
                            [
                            "flex w-full items-center gap-3 rounded-lg px-3 py-3 text-sm font-medium transition",
                            isActive
                                ? "bg-indigo-500/15 text-white ring-1 ring-indigo-400/20"
                                : "text-slate-300 hover:bg-slate-900 hover:text-white",
                            ].join(" ")
                        }
                        >
                        <Icon size={18} />
                        {item.label}
                        </NavLink>
                    )
                })}

              <NavLink
                to="/app/settings"
                onClick={() => setMobileOpen(false)}
                className={({ isActive }) =>
                    [
                    "mt-6 flex w-full items-center gap-3 rounded-lg px-3 py-3 text-sm font-medium transition",
                    isActive
                        ? "bg-indigo-500/15 text-white"
                        : "text-slate-300 hover:bg-slate-900 hover:text-white",
                    ].join(" ")
                }
                >
                <Settings size={18} />
                Settings
            </NavLink>
            </nav>
          </aside>
        </div>
      )}

      {/* Main area */}
      <div className="lg:pl-64">
        {/* Desktop topbar */}
        <header className="hidden h-16 items-center justify-between border-b border-slate-200 bg-white px-8 lg:flex">
          <div>
                <p className="text-sm font-semibold text-slate-900">
                    DataRise AI
                </p>

                <p className="text-xs text-slate-500">
                    AI Data Workspace
                </p>
            </div>

          <div className="flex items-center gap-3">
            <div className="max-w-64 truncate text-right">
              <p className="text-sm font-medium text-slate-900">
                {user.displayName}
              </p>

              <p className="text-xs text-slate-500">
                Development session
              </p>
            </div>

            <div className="flex h-9 w-9 items-center justify-center rounded-full bg-indigo-100 text-sm font-semibold text-indigo-700">
              {initials}
            </div>
          </div>
        </header>

        <main className="px-4 py-6 sm:px-6 lg:px-8 lg:py-8">
          {children}
        </main>
      </div>
    </div>
  )
}
