import type { Config } from "tailwindcss";

export default {
  content: ["./app/**/*.{js,ts,jsx,tsx,mdx}", "./components/**/*.{js,ts,jsx,tsx,mdx}"],
  theme: {
    extend: {
      colors: {
        ink: "#1b2924",
        pine: "#173d32",
        moss: "#2e6655",
        cream: "#f4f1e8",
        paper: "#fbfaf6",
        clay: "#e97943",
      },
      boxShadow: {
        card: "0 1px 2px rgba(22, 45, 37, .04), 0 12px 36px rgba(22, 45, 37, .06)",
      },
    },
  },
  plugins: [],
} satisfies Config;
