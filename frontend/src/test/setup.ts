import "@testing-library/jest-dom/vitest";
import "@/i18n";
import { cleanup, configure } from "@testing-library/react";
import { afterEach } from "vitest";

// Whole-suite runs share the machine with other workers, so asynchronous waits get more room than the 1 s default.
configure({ asyncUtilTimeout: 5000 });

// jsdom does not implement scrolling; the router calls it on navigation.
window.scrollTo = () => undefined;

afterEach(() => {
  cleanup();
});
