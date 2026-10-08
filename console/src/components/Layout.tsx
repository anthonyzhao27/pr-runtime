import { NavLink, Outlet } from "react-router-dom";

const link = ({ isActive }: { isActive: boolean }) =>
  `px-2.5 h-[30px] inline-flex items-center rounded text-[13px] font-medium transition-colors ${
    isActive ? "bg-accent/10 text-accent" : "text-fg/70 hover:text-fg hover:bg-panel"
  }`;

export function Layout() {
  return (
    <div className="min-h-screen flex flex-col">
      <header className="h-[44px] border-b border-line bg-bg/95 backdrop-blur sticky top-0 z-20">
        <div className="max-w-[1480px] mx-auto px-4 h-full flex items-center gap-4">
          <NavLink to="/" className="flex items-center gap-2 text-fg">
            <span className="inline-block h-[18px] w-[18px] rounded bg-accent" />
            <span className="font-semibold text-[14px] tracking-tight">pr-runtime</span>
          </NavLink>
          <nav className="flex items-center gap-1">
            <NavLink to="/" end className={link}>
              Tasks
            </NavLink>
            <NavLink to="/eval" className={link}>
              Eval
            </NavLink>
          </nav>
          <div className="ml-auto text-[11px] text-mute">pod-per-PR review runtime</div>
        </div>
      </header>
      <main className="flex-1">
        <div className="max-w-[1480px] mx-auto px-4 py-4">
          <Outlet />
        </div>
      </main>
    </div>
  );
}
