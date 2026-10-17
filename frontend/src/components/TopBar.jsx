import NotificationsMenu from "./NotificationsMenu.jsx";
import AccountMenu from "./AccountMenu.jsx";

// Navigation and the brand live in the sidebar; the top bar keeps the
// notifications and account controls at the top right.
export default function TopBar() {
  return (
    <header className="topbar">
      <div className="top-actions">
        <NotificationsMenu />
        <AccountMenu />
      </div>
    </header>
  );
}
