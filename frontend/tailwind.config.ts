import type { Config } from "tailwindcss";
import animate from "tailwindcss-animate";

const config: Config = {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    container: { center: true, padding: "1rem", screens: { "2xl": "1200px" } },
    extend: {
      colors: {
        tiba: {
          50: "var(--tiba-blue-50)",
          100: "var(--tiba-blue-100)",
          600: "var(--tiba-blue-600)",
          700: "var(--tiba-blue-700)",
          900: "var(--tiba-blue-900)",
        },
        sky: { 100: "var(--sky-100)", 500: "var(--sky-500)", 800: "var(--sky-800)" },
        uzima: {
          50: "var(--uzima-50)",
          100: "var(--uzima-100)",
          600: "var(--uzima-600)",
          700: "var(--uzima-700)",
        },
        jua: {
          50: "var(--jua-50)",
          100: "var(--jua-100)",
          400: "var(--jua-400)",
          700: "var(--jua-700)",
        },
        danger: { 50: "var(--danger-50)", 600: "var(--danger-600)", 700: "var(--danger-700)" },
        background: "var(--background)",
        foreground: "var(--foreground)",
        card: { DEFAULT: "var(--card)", foreground: "var(--card-foreground)" },
        popover: { DEFAULT: "var(--popover)", foreground: "var(--popover-foreground)" },
        primary: {
          DEFAULT: "var(--primary)",
          hover: "var(--primary-hover)",
          foreground: "var(--primary-foreground)",
        },
        secondary: { DEFAULT: "var(--secondary)", foreground: "var(--secondary-foreground)" },
        muted: { DEFAULT: "var(--muted)", foreground: "var(--muted-foreground)" },
        destructive: { DEFAULT: "var(--destructive)", foreground: "var(--destructive-foreground)" },
        border: "var(--border)",
        input: "var(--input)",
        ring: "var(--ring)",
      },
      borderRadius: { lg: "var(--radius)", md: "calc(var(--radius) - 2px)", sm: "calc(var(--radius) - 4px)" },
      boxShadow: { card: "var(--shadow-card)" },
      fontFamily: {
        sans: ['"Inter Variable"', "ui-sans-serif", "system-ui", "sans-serif"],
        heading: ['"Plus Jakarta Sans Variable"', '"Inter Variable"', "ui-sans-serif", "system-ui", "sans-serif"],
      },
      keyframes: {
        "accordion-down": { from: { height: "0" }, to: { height: "var(--radix-accordion-content-height)" } },
      },
    },
  },
  plugins: [animate],
};

export default config;
