import "@fontsource-variable/hanken-grotesk";
import "@fontsource-variable/literata";
import "@xyflow/react/dist/style.css";
import "./globals.css";

import type { Metadata } from "next";

import { TopBar } from "@/components/shell";

import { Providers } from "./providers";

export const metadata: Metadata = {
  title: "DriftTrace",
  description: "Follow a scientific claim back through its citations and see where its meaning changed.",
};

const themeScript = `try{var t=localStorage.getItem("drifttrace-theme");if(t==="dark"||(!t&&matchMedia("(prefers-color-scheme: dark)").matches))document.documentElement.classList.add("dark")}catch(e){}`;

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: themeScript }} />
      </head>
      <body>
        <Providers>
          <TopBar />
          {children}
        </Providers>
      </body>
    </html>
  );
}
