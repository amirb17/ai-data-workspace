import { StrictMode } from "react"
import { createRoot } from "react-dom/client"
import { BrowserRouter, HashRouter } from "react-router-dom"

import "./index.css"
import App from "./App"

const Router = import.meta.env.PROD &&
  import.meta.env.BASE_URL !== "/"
    ? HashRouter
    : BrowserRouter

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <Router>
      <App />
    </Router>
  </StrictMode>,
)
