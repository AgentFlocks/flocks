import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ProductNameProvider, useProductName } from "./ProductNameContext";

const { uiConfigApi } = vi.hoisted(() => ({
  uiConfigApi: {
    getDisplay: vi.fn(),
    resetFavicon: vi.fn(),
    update: vi.fn(),
    uploadFavicon: vi.fn(),
  },
}));

vi.mock("@/api/uiConfig", () => ({
  uiConfigApi,
}));

function ProductNames() {
  const { productName, displayName, configuredDisplayName, logoUrl } = useProductName();
  return (
    <div>
      <span data-testid="product-name">{productName}</span>
      <span data-testid="display-name">{displayName}</span>
      <span data-testid="configured-name">{configuredDisplayName ?? "(none)"}</span>
      <span data-testid="logo-url">{logoUrl ?? "none"}</span>
    </div>
  );
}

describe("ProductNameProvider", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("uses the server default name when no custom display name is configured", async () => {
    uiConfigApi.getDisplay.mockResolvedValue({
      displayName: "Flocks",
      configuredDisplayName: null,
      faviconUrl: null,
    });

    render(
      <ProductNameProvider>
        <ProductNames />
      </ProductNameProvider>,
    );

    await waitFor(() =>
      expect(screen.getByTestId("product-name")).toHaveTextContent("Flocks"),
    );
    expect(screen.getByTestId("display-name")).toHaveTextContent("Flocks");
    expect(screen.getByTestId("configured-name")).toHaveTextContent("(none)");
    expect(document.title).toBe("Flocks");
    expect(screen.getByTestId("logo-url")).toHaveTextContent("/favicon.svg");
  });

  it("uses the configured display name everywhere", async () => {
    uiConfigApi.getDisplay.mockResolvedValue({
      displayName: "Acme SOC",
      configuredDisplayName: "Acme SOC",
      faviconUrl: null,
    });

    render(
      <ProductNameProvider>
        <ProductNames />
      </ProductNameProvider>,
    );

    await waitFor(() =>
      expect(screen.getByTestId("product-name")).toHaveTextContent("Acme SOC"),
    );
    expect(screen.getByTestId("display-name")).toHaveTextContent("Acme SOC");
    expect(screen.getByTestId("configured-name")).toHaveTextContent("Acme SOC");
    expect(screen.getByTestId("logo-url")).toHaveTextContent("none");
  });

  it("falls back to the default name when the display config cannot be loaded", async () => {
    uiConfigApi.getDisplay.mockRejectedValue(new Error("offline"));

    render(
      <ProductNameProvider>
        <ProductNames />
      </ProductNameProvider>,
    );

    await waitFor(() =>
      expect(screen.getByTestId("product-name")).toHaveTextContent("Flocks"),
    );
  });
});
