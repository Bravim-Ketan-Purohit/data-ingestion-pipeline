import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Data Ingestion Pipeline",
  description: "Drag-and-drop normalization of messy PDFs and CSVs into strict JSON schemas",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body className="min-h-screen bg-background font-sans antialiased">
        <div className="relative flex min-h-screen flex-col">
          <header className="sticky top-0 z-50 w-full border-b bg-background/95 backdrop-blur">
            <div className="container flex h-14 items-center px-4">
              <nav className="flex items-center space-x-6">
                <a href="/" className="font-semibold">Pipeline</a>
                <a href="/upload" className="text-sm text-muted-foreground hover:text-foreground">Upload</a>
                <a href="/documents" className="text-sm text-muted-foreground hover:text-foreground">Documents</a>
                <a href="/schemas" className="text-sm text-muted-foreground hover:text-foreground">Schemas</a>
                <a href="/runs" className="text-sm text-muted-foreground hover:text-foreground">Runs</a>
                <a href="/timing" className="text-sm text-muted-foreground hover:text-foreground">Timing</a>
              </nav>
            </div>
          </header>
          <main className="flex-1">{children}</main>
        </div>
      </body>
    </html>
  );
}
