import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  darkMode: "class",
  theme: {
    extend: {
      colors: {
        page: "var(--bg-page)",
        card: "var(--bg-card)",
        border: "var(--border)",
        accent: {
          DEFAULT: "var(--accent)",
          light: "var(--accent-light)",
          bg: "var(--accent-bg)",
          border: "var(--accent-border)",
        },
        secondary: "var(--secondary)",
        txt: {
          primary: "var(--text-primary)",
          secondary: "var(--text-secondary)",
          tertiary: "var(--text-tertiary)",
        },
        ok: {
          DEFAULT: "var(--success)",
          bg: "var(--success-bg)",
        },
        warn: {
          DEFAULT: "var(--warning)",
          bg: "var(--warning-bg)",
        },
        err: {
          DEFAULT: "var(--error)",
          bg: "var(--error-bg)",
        },
      },
      fontFamily: {
        sans: [
          "system-ui",
          "-apple-system",
          "PingFang SC",
          "Hiragino Sans GB",
          "Microsoft YaHei",
          "sans-serif",
        ],
      },
    },
  },
  plugins: [],
};

export default config;
