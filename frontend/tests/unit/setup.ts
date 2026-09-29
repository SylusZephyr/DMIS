import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

// vitest runs without globals, so Testing Library's automatic cleanup is registered here.
afterEach(() => cleanup());
