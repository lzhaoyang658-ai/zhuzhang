import type { Metadata } from "next";
import { Geist } from "next/font/google";
import { ReauthDialog } from "@/components/reauth-dialog";
import "./globals.css";
import "./refero-v2.css";
import "./workspace-v4.css";

const geist = Geist({
  subsets: ["latin"],
  variable: "--font-geist",
  display: "swap",
});

export const metadata: Metadata = {
  title: "筑账 · 装修预算与增项管家",
  description: "把每一笔装修变化说明白",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="zh-CN" className={geist.variable}>
      <body>
        {children}
        <ReauthDialog />
      </body>
    </html>
  );
}
