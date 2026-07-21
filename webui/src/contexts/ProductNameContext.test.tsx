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
  const { productName, proProductName } = useProductName();
  return (
    <div>
      <span data-testid="product-name">{productName}</span>
      <span data-testid="pro-product-name">{proProductName}</span>
    </div>
  );
}

describe("ProductNameProvider", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("uses Flocks Pro for Pro surfaces when no custom display name is configured", async () => {
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
    expect(screen.getByTestId("pro-product-name")).toHaveTextContent(
      "Flocks Pro",
    );
  });

  it("uses the configured display name for Pro surfaces", async () => {
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
    expect(screen.getByTestId("pro-product-name")).toHaveTextContent(
      "Acme SOC",
    );
  });
});
