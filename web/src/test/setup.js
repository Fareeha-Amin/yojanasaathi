import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

afterEach(() => cleanup());

// jsdom has no layout / animation APIs the screens touch
window.scrollTo = () => {};
Element.prototype.scrollIntoView = function () {};
if (!window.requestAnimationFrame) {
  window.requestAnimationFrame = (cb) => setTimeout(() => cb(Date.now()), 16);
  window.cancelAnimationFrame = (id) => clearTimeout(id);
}
