import "@testing-library/jest-dom/vitest";
import { afterAll, afterEach, beforeAll } from "vitest";
import { resetMockStore } from "../mocks/handlers";
import { server } from "../mocks/server";

beforeAll(() => {
  server.listen({ onUnhandledRequest: "error" });
});

afterEach(() => {
  server.resetHandlers();
  resetMockStore();
  if (typeof window !== "undefined") {
    window.history.replaceState(null, "", "/");
  }
});

afterAll(() => {
  server.close();
});
