/// <reference types="vitest" />
import { defineConfig, mergeConfig } from "vitest/config";
import viteConfig from "./vite.config";

// Merge with vite config so vite plugins (react, vite-plugin-pwa stubs) are loaded in tests too.
export default mergeConfig(
  viteConfig,
  defineConfig({
    test: {
      globals: true,
      environment: "jsdom",
      setupFiles: ["./vitest.setup.ts"],
      css: false,
      // Don't bundle service workers in tests
      server: { deps: { inline: [/@react-three\/drei/] } },
    },
  }),
);
