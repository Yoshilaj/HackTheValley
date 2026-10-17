import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import App from "./App.jsx";
import { SessionProvider } from "./context/SessionContext.jsx";
import { ProfileProvider } from "./context/ProfileContext.jsx";
import { ChatHistoryProvider } from "./context/ChatHistoryContext.jsx";
import { CareAlertProvider } from "./context/CareAlertContext.jsx";
import { AppointmentsProvider } from "./context/AppointmentsContext.jsx";
import "./styles/global.css";

ReactDOM.createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <BrowserRouter>
      <SessionProvider>
        <ProfileProvider>
          <ChatHistoryProvider>
            <AppointmentsProvider>
              <CareAlertProvider>
                <App />
              </CareAlertProvider>
            </AppointmentsProvider>
          </ChatHistoryProvider>
        </ProfileProvider>
      </SessionProvider>
    </BrowserRouter>
  </React.StrictMode>
);
