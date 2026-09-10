import "@testing-library/jest-dom";

// @xyflow/react relies on ResizeObserver and DOMRect measurement when
// rendered under jsdom. jsdom itself does not provide either, so we add
// minimal polyfills so component-level tests can mount React Flow without
// crashing.
if (typeof (globalThis as { ResizeObserver?: unknown }).ResizeObserver === "undefined") {
  class ResizeObserverPolyfill {
    observe() {}
    unobserve() {}
    disconnect() {}
  }
  (globalThis as unknown as { ResizeObserver: typeof ResizeObserverPolyfill }).ResizeObserver =
    ResizeObserverPolyfill;
}

if (typeof window !== "undefined") {
  if (!("matchMedia" in window)) {
    // Some libraries call window.matchMedia on mount; jsdom does not
    // implement it. Provide a no-op stub so React Flow can render.
    Object.defineProperty(window, "matchMedia", {
      writable: true,
      value: (query: string) => ({
        matches: false,
        media: query,
        onchange: null,
        addListener: () => {},
        removeListener: () => {},
        addEventListener: () => {},
        removeEventListener: () => {},
        dispatchEvent: () => false,
      }),
    });
  }

  // Provide a simple getBoundingClientRect if not implemented for the
  // current element, otherwise React Flow's measured size lookup fails.
  if (!HTMLElement.prototype.getBoundingClientRect) {
    HTMLElement.prototype.getBoundingClientRect = function () {
      return {
        x: 0,
        y: 0,
        top: 0,
        left: 0,
        bottom: 0,
        right: 0,
        width: 800,
        height: 600,
        toJSON: () => ({}),
      } as DOMRect;
    };
  }
}