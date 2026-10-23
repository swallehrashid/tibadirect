import "@testing-library/jest-dom/vitest";
import "@/i18n";
import { cleanup, configure } from "@testing-library/react";
import { afterEach } from "vitest";

// Whole-suite runs share the machine with other workers, so asynchronous waits get more room than the 1 s default.
configure({ asyncUtilTimeout: 5000 });

// jsdom does not implement scrolling; the router calls it on navigation.
window.scrollTo = () => undefined;

// Radix Select and menus call pointer and scroll APIs that jsdom lacks.
Element.prototype.hasPointerCapture ??= () => false;
Element.prototype.setPointerCapture ??= () => undefined;
Element.prototype.releasePointerCapture ??= () => undefined;
Element.prototype.scrollIntoView ??= () => undefined;

// Radix measures form controls with ResizeObserver.
globalThis.ResizeObserver ??= class {
  observe() {}
  unobserve() {}
  disconnect() {}
};

afterEach(() => {
  cleanup();
});
