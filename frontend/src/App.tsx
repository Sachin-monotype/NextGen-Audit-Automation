import { useEffect, useState } from "react";
import { fetchHealth } from "./api";
import DisplayPage from "./pages/DisplayPage";
import GeneratePage from "./pages/GeneratePage";
import ComparePage from "./pages/ComparePage";
import ResultsPage from "./pages/ResultsPage";
import HealthPage from "./pages/HealthPage";

export type Section = "generate" | "display" | "compare" | "result" | "health";
export type Theme = "dark" | "light";

const NAV: { id: Section; label: string; hint: string }[] = [
  { id: "generate", label: "Generate", hint: "Run audit pipeline" },
  { id: "display", label: "Enrich/raw", hint: "Browse collections" },
  { id: "compare", label: "Compare", hint: "Pick operations" },
  { id: "result", label: "Result", hint: "Source vs enrich" },
  { id: "health", label: "API Health", hint: "Test connectivity" },
];

const SECTION_KEY = "audit_active_section";
const COMPARE_JOB_KEY = "audit_compare_job_id";

function getInitialTheme(): Theme {
  const stored = localStorage.getItem("audit-theme");
  if (stored === "light" || stored === "dark") return stored;
  return "dark";
}

function getInitialSection(): Section {
  const stored = localStorage.getItem(SECTION_KEY);
  if (stored && NAV.some((n) => n.id === stored)) return stored as Section;
  return "display";
}

export default function App() {
  const [section, setSection] = useState<Section>(getInitialSection);
  const [healthy, setHealthy] = useState<boolean | null>(null);
  const [lastCompareJobId, setLastCompareJobId] = useState<string | null>(() =>
    localStorage.getItem(COMPARE_JOB_KEY),
  );
  /** Compare job id that the Compare page should adopt & show live (from Generation Status). */
  const [compareAdoptId, setCompareAdoptId] = useState<string | null>(null);
  /** Operations from the last compare — Result page highlights just these. */
  const [comparedOps, setComparedOps] = useState<string[] | null>(null);
  const [theme, setTheme] = useState<Theme>(getInitialTheme);

  useEffect(() => {
    document.documentElement.setAttribute("data-theme", theme);
    localStorage.setItem("audit-theme", theme);
  }, [theme]);

  useEffect(() => {
    localStorage.setItem(SECTION_KEY, section);
  }, [section]);

  useEffect(() => {
    fetchHealth()
      .then((h) => setHealthy(h.mongo === true))
      .catch(() => setHealthy(false));
  }, []);

  function goToSection(next: Section) {
    setSection(next);
  }

  function onCompareCompleted(jobId: string, operations?: string[]) {
    setLastCompareJobId(jobId);
    localStorage.setItem(COMPARE_JOB_KEY, jobId);
    setComparedOps(operations && operations.length ? operations : null);
    setSection("result");
  }

  /** Generation Status → "Compare selected PASS": start the job, then land on Compare (live). */
  function onCompareRequested(jobId: string) {
    setLastCompareJobId(jobId);
    localStorage.setItem(COMPARE_JOB_KEY, jobId);
    setCompareAdoptId(jobId);
    setSection("compare");
  }

  return (
    <div className="layout">
      <aside className="sidebar">
        <div className="brand">
          <h1>NextGen Audit</h1>
          <p>Automation</p>
        </div>
        <nav>
          {NAV.map((item) => (
            <button
              key={item.id}
              type="button"
              className={section === item.id ? "nav-btn active" : "nav-btn"}
              onClick={() => goToSection(item.id)}
            >
              <span>{item.label}</span>
              <small>{item.hint}</small>
            </button>
          ))}
        </nav>
        <div className="sidebar-footer">
          <button
            type="button"
            className="theme-toggle"
            onClick={() => setTheme((t) => (t === "dark" ? "light" : "dark"))}
            title="Toggle theme"
          >
            {theme === "dark" ? "☀ Light" : "☾ Dark"}
          </button>
          <div className="health-status">
            <span className={healthy ? "dot ok" : "dot bad"} />
            {healthy === null ? "Checking…" : healthy ? "Mongo connected" : "Mongo offline"}
          </div>
        </div>
      </aside>

      <main className="content">
        {/* Keep pages mounted so Compare/Generate job logs survive tab switches. */}
        <div className={section === "generate" ? "section-panel" : "section-panel hidden"}>
          <GeneratePage
            onCompareCompleted={onCompareCompleted}
            onCompareRequested={onCompareRequested}
          />
        </div>
        <div className={section === "display" ? "section-panel" : "section-panel hidden"}>
          <DisplayPage onCompareRequested={onCompareRequested} />
        </div>
        <div className={section === "compare" ? "section-panel" : "section-panel hidden"}>
          <ComparePage onCompareCompleted={onCompareCompleted} adoptJobId={compareAdoptId} />
        </div>
        <div className={section === "result" ? "section-panel" : "section-panel hidden"}>
          <ResultsPage initialJobId={lastCompareJobId} highlightOperations={comparedOps} />
        </div>
        <div className={section === "health" ? "section-panel" : "section-panel hidden"}>
          <HealthPage />
        </div>
      </main>
    </div>
  );
}
