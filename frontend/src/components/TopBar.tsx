"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";

export default function TopBar() {
  const path = usePathname();
  return (
    <div className="topbar">
      <h1>OLLIVE</h1>
      <nav className="nav">
        <Link href="/" className={path === "/" ? "active" : ""}>
          Chat
        </Link>
        <Link
          href="/dashboard"
          className={path?.startsWith("/dashboard") ? "active" : ""}
        >
          Dashboard
        </Link>
      </nav>
    </div>
  );
}
