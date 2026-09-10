import React from "react";
import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { createAuditApi } from "../../../.flocks/flockshub/plugins/webuis/code_security_ui/code-security-workspace/src/api";
import {
  BatchContext,
  useAuditApi,
} from "../../../.flocks/flockshub/plugins/webuis/code_security_ui/code-security-workspace/src/BatchContext";

afterEach(() => {
  delete (globalThis as any).__FLOCKS_WEBUI_CONTRACT_SDK__;
});

describe("isolated batch audit API", () => {
  it("keeps simultaneous components with the same scan ID bound to separate task APIs", async () => {
    const get = vi.fn(async (url: string) => ({
      data: { owner: url.includes("/tasks/1/") ? "one" : "two" },
    }));
    (globalThis as any).__FLOCKS_WEBUI_CONTRACT_SDK__ = { api: { get } };
    function Probe() {
      const api = useAuditApi();
      const [owner, setOwner] = React.useState("");
      React.useEffect(() => {
        api.getScan("same-scan").then((value: any) => setOwner(value.owner));
      }, [api]);
      return <span>{owner}</span>;
    }
    render(
      <>
        <BatchContext.Provider value={{ batchId: "batch_a", taskId: "1" }}>
          <Probe />
        </BatchContext.Provider>
        <BatchContext.Provider value={{ batchId: "batch_a", taskId: "2" }}>
          <Probe />
        </BatchContext.Provider>
      </>,
    );
    await waitFor(() => {
      expect(screen.getByText("one")).toBeInTheDocument();
      expect(screen.getByText("two")).toBeInTheDocument();
    });
    expect(get.mock.calls.map((args) => args[0])).toEqual([
      "/api/code-security/v1/batches/batch_a/tasks/1/scans/same-scan",
      "/api/code-security/v1/batches/batch_a/tasks/2/scans/same-scan",
    ]);
  });

  it("scopes events, evidence, cancellation and downloads without affecting ordinary audits", async () => {
    const get = vi.fn(async () => ({ data: { items: [] } }));
    const post = vi.fn(async () => ({ data: {} }));
    (globalThis as any).__FLOCKS_WEBUI_CONTRACT_SDK__ = { api: { get, post } };
    const base = "/api/code-security/v1/batches/batch_a/tasks/2";
    const api = createAuditApi(base);
    await api.listScans();
    await api.getEvents("scan");
    await api.getArtifact("scan", "findings");
    await api.getEvidence("scan", "evidence");
    await api.cancelScan("scan");
    expect(
      get.mock.calls.every((args) =>
        (args as unknown as string[])[0].startsWith(base),
      ),
    ).toBe(true);
    expect(post).toHaveBeenCalledWith(`${base}/scans/scan/cancel`);
    expect(api.downloadUrl("scan", "report.md")).toBe(
      `${base}/scans/scan/downloads/report.md`,
    );
    await createAuditApi().getScan("scan");
    expect(get).toHaveBeenLastCalledWith("/api/code-security/v1/scans/scan");
  });
});
