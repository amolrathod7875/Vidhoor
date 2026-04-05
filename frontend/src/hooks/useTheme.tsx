import { createContext, useContext, useEffect, useState, ReactNode } from "react";

type Theme = "light" | "dark" | "system";
type ResolvedTheme = "light" | "dark";

const ThemeContext = createContext<{
  theme: Theme;
  resolvedTheme: ResolvedTheme;
  setTheme: (theme: Theme) => void;
  toggle: () => void;
}>({
  theme: "dark",
  resolvedTheme: "dark",
  setTheme: () => {},
  toggle: () => {},
});

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [theme, setTheme] = useState<Theme>(() => {
    if (typeof window !== "undefined") {
      const stored = localStorage.getItem("vidhoor-theme") as Theme | null;
      if (stored === "light" || stored === "dark" || stored === "system") {
        return stored;
      }
      return "system";
    }
    return "system";
  });
  const [resolvedTheme, setResolvedTheme] = useState<ResolvedTheme>("dark");

  useEffect(() => {
    const media = window.matchMedia("(prefers-color-scheme: dark)");

    const applyTheme = () => {
      const nextResolved: ResolvedTheme =
        theme === "system" ? (media.matches ? "dark" : "light") : theme;
      setResolvedTheme(nextResolved);
      document.documentElement.classList.toggle("dark", nextResolved === "dark");
    };

    applyTheme();

    const handleSystemThemeChange = () => {
      if (theme === "system") {
        applyTheme();
      }
    };

    media.addEventListener("change", handleSystemThemeChange);

    localStorage.setItem("vidhoor-theme", theme);
    return () => {
      media.removeEventListener("change", handleSystemThemeChange);
    };
  }, [theme]);

  const toggle = () => setTheme((t) => (t === "light" ? "dark" : "light"));

  return (
    <ThemeContext.Provider value={{ theme, resolvedTheme, setTheme, toggle }}>
      {children}
    </ThemeContext.Provider>
  );
}

export const useTheme = () => useContext(ThemeContext);
