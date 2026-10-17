import { useEffect, useState } from "react";
import { getHealth } from "../api/client.js";
import "../styles/home.css";

export default function Home() {
  const [status, setStatus] = useState("checking...");

  useEffect(() => {
    getHealth()
      .then((data) => setStatus(data.status))
      .catch(() => setStatus("backend offline"));
  }, []);

  return (
    <main className="home">
      <h1>HackTheValley</h1>
      <p>
        Backend status: <strong>{status}</strong>
      </p>
    </main>
  );
}
