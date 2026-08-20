import React, { useState } from "react";

export default function DownloadPage() {
  const [environment, setEnvironment] = useState<"preprod" | "qa">("preprod");
  const [dataType, setDataType] = useState<"raw" | "enriched">("raw");
  const [direction, setDirection] = useState<"inbound" | "outbound">("inbound");
  const [correlationId, setCorrelationId] = useState("");

  const handleDownload = () => {
    if (!correlationId.trim()) {
      alert("Please enter a correlation ID");
      return;
    }

    // Replace "preprod" with "qa" based on selected environment
    const envString = environment === "preprod" ? "preprod" : "qa";
    
    // The base URL from the user's example
    const baseUrl = `https://mt-audit-log-resolver-service-${envString}.monotype-pp.com/v1/payload-dumps`;
    
    // Construct the URL with query parameters
    const url = new URL(baseUrl);
    url.searchParams.append("type", direction); // Inbound or outbound
    url.searchParams.append("correlation-id", correlationId.trim());
    
    // According to the user, "raw and enrich should be dropdown"
    url.searchParams.append("dataType", dataType);

    window.open(url.toString(), "_blank");
  };

  return (
  <div className="page download-page fade-in">
    <div className="download-header">
      <div>

        <p className="page-desc">
          Retrieve raw or enriched payload data using your environment and
          correlation ID.
        </p>
      </div>

    </div>

    <div className="card download-card">
      <div className="card-header">
        <div>
          <h3>Export Configuration</h3>
          <span>Select the payload you want to retrieve.</span>
        </div>
      </div>

      <div className="form-grid">
        <div className="form-group">
          <label>Environment</label>
          <select
            value={environment}
            onChange={(e) =>
              setEnvironment(e.target.value as "preprod" | "qa")
            }
          >
            <option value="preprod">Preprod</option>
            <option value="qa">QA</option>
          </select>
        </div>

        <div className="form-group">
          <label>Data Type</label>
          <select
            value={dataType}
            onChange={(e) =>
              setDataType(e.target.value as "raw" | "enriched")
            }
          >
            <option value="raw">Raw</option>
            <option value="enriched">Enriched</option>
          </select>
        </div>

        <div className="form-group">
          <label>Direction</label>
          <select
            value={direction}
            onChange={(e) =>
              setDirection(e.target.value as "inbound" | "outbound")
            }
          >
            <option value="inbound">Inbound</option>
            <option value="outbound">Outbound</option>
          </select>
        </div>
      </div>

      <div className="divider" />

      <div className="form-group correlation-group">
        <label>
          Correlation ID
          <span className="label-hint">Required</span>
        </label>

        <div className="input-wrapper">
          <span className="input-icon">#</span>

          <input
            type="text"
            placeholder="624f678d-8d2c-40e9-93a9-62a587caa055"
            value={correlationId}
            onChange={(e) => setCorrelationId(e.target.value)}
          />
        </div>

        <small>
          Enter the correlation ID associated with the payload you want to
          download.
        </small>
      </div>

      <div className="download-footer">
        <div className="selection-summary">
          <span className="status-dot" />
          <span>
            {environment.toUpperCase()} · {dataType.toUpperCase()} ·{" "}
            {direction.toUpperCase()}
          </span>
        </div>

        <button className="btn primary download-btn" onClick={handleDownload}>
          <span>↓</span>
          Download Data
        </button>
      </div>
    </div>
  </div>
);
}