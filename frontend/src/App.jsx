import { useEffect, useState } from "react";
import { Navigate, Route, Routes, useLocation } from "react-router-dom";
import TopBar from "./components/TopBar.jsx";
import HistorySidebar from "./components/HistorySidebar.jsx";
import Overview from "./pages/Overview.jsx";
import VitalsPage from "./pages/VitalsPage.jsx";
import AppointmentsPage from "./pages/AppointmentsPage.jsx";
import MedicationsPage from "./pages/MedicationsPage.jsx";
import ChatPage from "./pages/ChatPage.jsx";
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

// Remembered choice wins; otherwise start collapsed on narrower screens.
function useSidebarCollapsed() {
  const [collapsed, setCollapsed] = useState(() => {
    try {
      const saved = localStorage.getItem(SIDEBAR_KEY);
      if (saved !== null) return saved === "1";
    } catch {
      /* storage unavailable */
    }
    return window.matchMedia("(max-width: 1199px)").matches;
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

export default function App() {
  useRouteEffects();
  const [collapsed, toggleSidebar] = useSidebarCollapsed();

  return (
    <div className="app">
      <TopBar />
      <div className={`workspace ${collapsed ? "history-collapsed" : ""}`}>
        <HistorySidebar collapsed={collapsed} onToggle={toggleSidebar} />
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
    </div>
  );
}
