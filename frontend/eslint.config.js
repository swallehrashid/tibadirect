import js from "@eslint/js";
import jsxA11y from "eslint-plugin-jsx-a11y";
import reactHooks from "eslint-plugin-react-hooks";
import reactRefresh from "eslint-plugin-react-refresh";
import globals from "globals";
import tseslint from "typescript-eslint";

export default tseslint.config(
  { ignores: ["dist", "coverage", "playwright-report", "test-results", "src/api/schema.d.ts"] },
  {
    files: ["**/*.{ts,tsx}"],
    extends: [js.configs.recommended, ...tseslint.configs.recommended, jsxA11y.flatConfigs.recommended],
    languageOptions: { ecmaVersion: 2022, globals: globals.browser },
    plugins: { "react-hooks": reactHooks, "react-refresh": reactRefresh },
    rules: {
      ...reactHooks.configs.recommended.rules,
      "react-refresh/only-export-components": ["warn", { allowConstantExport: true }],
      "@typescript-eslint/consistent-type-imports": "error",
    },
  },
  {
    // shadcn-style primitives export their style helpers (buttonVariants, toast) beside the component.
    files: ["src/components/ui/**"],
    rules: { "react-refresh/only-export-components": "off" },
  },
  {
    files: ["vite.config.ts", "tailwind.config.ts", "playwright.config.ts", "postcss.config.js", "eslint.config.js", "e2e/**"],
    languageOptions: { globals: globals.node },
  },
);
