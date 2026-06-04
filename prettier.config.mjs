/** @type {import("prettier").Config} */
export default {
  semi: true,
  singleQuote: false,
  trailingComma: "all",
  printWidth: 100,
  tabWidth: 2,
  useTabs: false,
  arrowParens: "always",
  endOfLine: "lf",
  bracketSpacing: true,
  plugins: [],
  overrides: [
    {
      files: "*.md",
      options: { proseWrap: "preserve" },
    },
    {
      files: "*.{yml,yaml}",
      options: { tabWidth: 2, singleQuote: false },
    },
  ],
};
