import i18n from "i18next";
import { initReactI18next } from "react-i18next";
import en from "./en.json";
import sw from "./sw.json";

export const LANGUAGES = ["en", "sw"] as const;
export type Language = (typeof LANGUAGES)[number];
const STORAGE_KEY = "tiba.lang";

function readStored(): Language | null {
  try {
    const value = localStorage.getItem(STORAGE_KEY);
    return LANGUAGES.find((code) => code === value) ?? null;
  } catch {
    return null; // storage can be blocked (private windows); the default still works
  }
}

/** The saved choice, else the browser's language when we have it, else English. */
export function initialLanguage(): Language {
  const stored = readStored();
  if (stored) return stored;
  const preferred = typeof navigator === "undefined" ? [] : (navigator.languages ?? [navigator.language]);
  for (const tag of preferred) {
    const match = LANGUAGES.find((code) => tag?.toLowerCase().startsWith(code));
    if (match) return match;
  }
  return "en";
}

export function setLanguage(language: Language): void {
  try {
    localStorage.setItem(STORAGE_KEY, language);
  } catch {
    // Not saving the choice is harmless.
  }
  void i18n.changeLanguage(language);
  document.documentElement.lang = language;
}

void i18n.use(initReactI18next).init({
  resources: { en: { translation: en }, sw: { translation: sw } },
  lng: initialLanguage(),
  fallbackLng: "en",
  interpolation: { escapeValue: false },
});
document.documentElement.lang = i18n.language;

export default i18n;
