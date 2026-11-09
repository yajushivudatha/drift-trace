import type { Config } from "tailwindcss";
import animate from "tailwindcss-animate";

const c = (name: string) => `var(--${name})`;

export default {
  darkMode: "class",
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}", "./lib/**/*.ts"],
  theme: {
    extend: {
      colors: {
        bg: c("bg"),
        surface: c("surface"),
        sunken: c("sunken"),
        ink: c("ink"),
        "ink-2": c("ink-2"),
        "ink-3": c("ink-3"),
        line: c("line"),
        accent: c("accent"),
        "accent-ink": c("accent-ink"),
        "accent-soft": c("accent-soft"),
        d0: c("d0"), "d0-soft": c("d0-soft"),
        d1: c("d1"), "d1-soft": c("d1-soft"),
        d2: c("d2"), "d2-soft": c("d2-soft"),
        d3: c("d3"), "d3-soft": c("d3-soft"),
        dx: c("dx"), "dx-soft": c("dx-soft"),
      },
      fontFamily: {
        sans: ['"Hanken Grotesk Variable"', "system-ui", "sans-serif"],
        serif: ['"Literata Variable"', "Georgia", "serif"],
      },
      fontSize: {
        "2xs": ["0.6875rem", { lineHeight: "1rem" }],
      },
    },
  },
  plugins: [animate],
} satisfies Config;
