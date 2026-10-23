import { AllCommunityModule, ModuleRegistry, themeQuartz } from "ag-grid-community";

ModuleRegistry.registerModules([AllCommunityModule]);

/** AG Grid dressed in the TibaDirect tokens (kept as literal values: the grid's theme API takes colours, not CSS variables). */
export const tibaGridTheme = themeQuartz.withParams({
  accentColor: "#1D5FD1",
  backgroundColor: "#ffffff",
  foregroundColor: "#0A2463",
  headerBackgroundColor: "#EEF4FD",
  headerTextColor: "#0A2463",
  borderColor: "#E2E8F0",
  oddRowBackgroundColor: "#F8FAFC",
  rowHoverColor: "#DCE8FB",
  fontFamily: '"Inter Variable", ui-sans-serif, system-ui, sans-serif',
  headerFontFamily: '"Plus Jakarta Sans Variable", "Inter Variable", sans-serif',
  fontSize: 14,
  headerFontSize: 13,
  headerFontWeight: 700,
  wrapperBorderRadius: 10,
  spacing: 8,
});
