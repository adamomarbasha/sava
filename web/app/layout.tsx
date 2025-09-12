import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import "./globals.css";
import { AuthProvider } from "./contexts/AuthContext";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "Sava - Bookmark Manager",
  description: "Save and organize your bookmarks",
  icons: {
    icon: [
      { url: "/favicon.ico", sizes: "any" },
      { url: "/savaFav.png", sizes: "512x512", type: "image/png" },
      { url: "/savaFav.png", sizes: "256x256", type: "image/png" },
      { url: "/savaFav.png", sizes: "192x192", type: "image/png" },
      { url: "/savaFav.png", sizes: "128x128", type: "image/png" },
      { url: "/savaFav.png", sizes: "64x64", type: "image/png" },
      { url: "/savaFav.png", sizes: "32x32", type: "image/png" },
      { url: "/savaFav.png", sizes: "16x16", type: "image/png" },
    ],
    apple: "/savaFav.png",
    shortcut: "/favicon.ico",
  },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body
        className={`${geistSans.variable} ${geistMono.variable} antialiased`}
      >
        <AuthProvider>
          {children}
        </AuthProvider>
      </body>
    </html>
  );
}
