import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import App from "./App.jsx";
import { ChatHistoryProvider } from "./context/ChatHistoryContext.jsx";
import "./styles/global.css";

ReactDOM.createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <BrowserRouter>
      <ChatHistoryProvider>
        <App />
      </ChatHistoryProvider>
    </BrowserRouter>
  </React.StrictMode>
);
