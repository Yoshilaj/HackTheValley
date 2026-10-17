import { useCallback, useEffect, useState } from "react";
import { Navigate, Route, Routes, useLocation } from "react-router-dom";
import TopBar from "./components/TopBar.jsx";
import HistorySidebar from "./components/HistorySidebar.jsx";
import Overview from "./pages/Overview.jsx";
import VitalsPage from "./pages/VitalsPage.jsx";
import AppointmentsPage from "./pages/AppointmentsPage.jsx";
import MedicationsPage from "./pages/MedicationsPage.jsx";
import ChatPage from "./pages/ChatPage.jsx";
import AutoBookSheet from "./components/AutoBookSheet.jsx";
import DevRiskControl from "./components/DevRiskControl.jsx";
import Landing from "./pages/Landing.jsx";
import SignInSplash from "./components/SignInSplash.jsx";
import usePrefersReducedMotion from "./hooks/usePrefersReducedMotion.js";
import { useSession } from "./context/SessionContext.jsx";
import { navItems } from "./data/placeholder.js";
import "./styles/dashboard.css";
import "./styles/pages.css";
import "./styles/chat.css";

const SIDEBAR_KEY = "caresync-history-collapsed";

// On a section change: reset scroll, set the tab title and move focus to the page heading.
// Keyed on the first path segment so moving between /chat/:id conversations doesn't steal focus.
function useRouteEffects() {
  const { pathname } = useLocation();
  const section = pathname.split("/")[1] ?? "";
  useEffect(() => {
    const item = navItems.find((n) => n.to === `/${section}`);
    document.title = item && item.to !== "/" ? `${item.label} | CareSync` : "CareSync Health Dashboard";
    window.scrollTo(0, 0);
    document.querySelector("main h1")?.focus({ preventScroll: true });
  }, [section]);
}

// Remembered choice wins; otherwise start collapsed so the sidebar only opens when asked.
function useSidebarCollapsed() {
  const [collapsed, setCollapsed] = useState(() => {
    try {
      const saved = localStorage.getItem(SIDEBAR_KEY);
      if (saved !== null) return saved === "1";
    } catch {
      /* storage unavailable */
    }
    return true;
  });

  const toggle = () =>
    setCollapsed((c) => {
      try {
        localStorage.setItem(SIDEBAR_KEY, c ? "0" : "1");
      } catch {
        /* storage unavailable */
      }
      return !c;
    });

  return [collapsed, toggle];
}

// The landing page (with Log in / Sign up) until there is a session: signed
// in or a guest. Then the dashboard, at whatever address was opened.
export default function App() {
  const { session } = useSession();
  const reduceMotion = usePrefersReducedMotion();
  const [splash, setSplash] = useState(false);
  const [prevSession, setPrevSession] = useState(session);

  // Just signed in (not a reload with a saved session): play the logo splash.
  // Set during render so the dashboard never shows a frame without it.
  if (session !== prevSession) {
    setPrevSession(session);
    if (session && !prevSession && !reduceMotion) setSplash(true);
  }

  const endSplash = useCallback(() => setSplash(false), []);

  if (!session) return <Landing />;
  return (
    <>
      <Dashboard />
      {splash && <SignInSplash onDone={endSplash} />}
    </>
  );
}

function Dashboard() {
  useRouteEffects();
  const [collapsed, toggleSidebar] = useSidebarCollapsed();

  // The sidebar carries the main navigation, so it shows on every page.
  return (
    <div className={`shell with-history ${collapsed ? "rail" : ""}`}>
      <HistorySidebar collapsed={collapsed} onToggle={toggleSidebar} />
      <div className="app">
        <TopBar />
        <div className="content">
          <Routes>
            <Route path="/" element={<Overview />} />
            <Route path="/vitals" element={<VitalsPage />} />
            <Route path="/appointments" element={<AppointmentsPage />} />
            <Route path="/medications" element={<MedicationsPage />} />
            <Route path="/chat" element={<ChatPage />} />
            <Route path="/chat/:id" element={<ChatPage />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </div>
      </div>
      <AutoBookSheet />
      {import.meta.env.DEV && <DevRiskControl />}
    </div>
  );
}
