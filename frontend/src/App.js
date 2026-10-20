import "./App.css";
import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import ShieldPage from "./pages/ShieldPage";
import LandingPage from "./pages/LandingPage";
import NcDocsPage from "./pages/NcDocsPage";

// One single NullCash site: every URL is served by the Bifrost v1 mirror
// (wallet, shield, trade, send, private route, privacy swap, track, docs, about).
function App() {
  return (
    <div className="App">
      <BrowserRouter>
        <Routes>
          <Route path="/" element={<LandingPage />} />
          <Route path="/docs" element={<NcDocsPage />} />
          <Route path="/app/api" element={<Navigate to="/docs" replace />} />
          <Route path="/app/api/*" element={<Navigate to="/docs" replace />} />
          <Route path="*" element={<ShieldPage />} />
        </Routes>
      </BrowserRouter>
    </div>
  );
}

export default App;
