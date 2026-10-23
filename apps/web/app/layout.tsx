import "./globals.css";
import type { ReactNode } from "react";

export const metadata = { title: "OmniMed AI", description: "Integrated clinical, diagnostic, pharmacy and life-sciences platform (synthetic demo)" };

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
