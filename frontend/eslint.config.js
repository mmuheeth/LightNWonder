import js from "@eslint/js";
import reactHooks from "eslint-plugin-react-hooks";
import reactRefresh from "eslint-plugin-react-refresh";
import globals from "globals";

export default [
  { ignores: ["dist/**", "coverage/**", "node_modules/**"] },

  js.configs.recommended,
  // Note the `.flat` namespace: the top-level `configs["recommended-latest"]`
  // is still eslintrc-shaped (plugins as an array) and ESLint 9+ rejects it.
  reactHooks.configs.flat["recommended-latest"],
  reactRefresh.configs.vite,

  {
    files: ["**/*.{js,jsx}"],
    languageOptions: {
      ecmaVersion: 2022,
      sourceType: "module",
      globals: { ...globals.browser, ...globals.es2022 },
      parserOptions: {
        ecmaFeatures: { jsx: true },
      },
    },
    rules: {
      "no-unused-vars": [
        "error",
        // Allow intentionally unused caught errors and React-style constants.
        { varsIgnorePattern: "^[A-Z_]", argsIgnorePattern: "^_", caughtErrors: "none" },
      ],
      "no-console": ["warn", { allow: ["warn", "error"] }],
      eqeqeq: ["error", "always", { null: "ignore" }],
      "prefer-const": "error",
      "object-shorthand": "error",
    },
  },

  // shadcn/ui components are vendored via `npx shadcn add` and intentionally
  // export a `cva` variants object next to the component. Don't fight the
  // generator over a fast-refresh nicety in files we don't hand-maintain.
  {
    files: ["src/components/ui/**/*.jsx"],
    rules: { "react-refresh/only-export-components": "off" },
  },

  // Node context: config files run outside the browser.
  {
    files: ["vite.config.js", "eslint.config.js"],
    languageOptions: { globals: globals.node },
  },

  // Tests get the Vitest globals (globals: true in vite.config.js).
  {
    files: ["**/*.test.{js,jsx}", "src/test/**"],
    languageOptions: { globals: { ...globals.browser, ...globals.vitest } },
  },
];
