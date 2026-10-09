import js from "@eslint/js";
import jsxA11y from "eslint-plugin-jsx-a11y";
import reactHooks from "eslint-plugin-react-hooks";
import globals from "globals";
import tseslint from "typescript-eslint";

export default tseslint.config(
  { ignores: ["dist", "node_modules"] },
  js.configs.recommended,
  ...tseslint.configs.strict,
  {
    languageOptions: {
      globals: { ...globals.browser },
    },
  },
  {
    files: ["*.config.ts", "*.config.js", "scripts/**/*.ts", "**/*.test.ts"],
    languageOptions: {
      globals: { ...globals.node },
    },
  },
  // Accessibility of the markup (the plugin's recommended set) and the two rules of hooks. Both are
  // errors: CI runs `eslint .`, which fails on an error and would let a warning through.
  {
    files: ["src/**/*.tsx", "src/**/*.ts"],
    ...jsxA11y.flatConfigs.recommended,
  },
  {
    files: ["src/**/*.tsx"],
    rules: {
      // The panel's tables spell their roles out (panel/DataTable.tsx): the style sheet stacks the rows
      // into cards on a narrow screen, and a browser drops a table's meaning when its display changes
      // unless the roles say it. These are the table's own roles and no others.
      "jsx-a11y/no-redundant-roles": ["error", { thead: ["rowgroup"], tbody: ["rowgroup"], tfoot: ["rowgroup"] }],
      "jsx-a11y/no-interactive-element-to-noninteractive-role": [
        "error",
        { th: ["columnheader", "rowheader"], td: ["cell"] },
      ],
    },
  },
  {
    files: ["src/**/*.tsx", "src/**/*.ts"],
    plugins: { "react-hooks": reactHooks },
    rules: {
      "react-hooks/rules-of-hooks": "error",
      "react-hooks/exhaustive-deps": "error",
    },
  },
);
