"use client";

import React from "react";

type ButtonProps = React.ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "primary" | "secondary" | "danger" | "ghost";
};

export function Button({ variant = "primary", className = "", ...props }: ButtonProps) {
  const base =
    "inline-flex items-center justify-center rounded-lg px-4 py-2 text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--brand)] disabled:opacity-50 disabled:pointer-events-none";
  const variants: Record<NonNullable<ButtonProps["variant"]>, string> = {
    primary: "bg-[var(--brand)] text-white hover:bg-[var(--brand-dark)]",
    secondary: "bg-[var(--muted)] text-[var(--foreground)] hover:bg-[#eef2f7]",
    danger: "bg-red-600 text-white hover:bg-red-700",
    ghost: "bg-transparent text-[var(--foreground)] hover:bg-[#f3f4f6]",
  };
  return <button className={`${base} ${variants[variant]} ${className}`} {...props} />;
}

type InputProps = React.InputHTMLAttributes<HTMLInputElement> & { leftIcon?: React.ReactNode };

export const Input = React.forwardRef<HTMLInputElement, InputProps>(
  ({ className = "", leftIcon, ...props }, ref) => {
    return (
      <div className="relative">
        {leftIcon && (
          <span className="absolute left-3 top-1/2 -translate-y-1/2 text-[var(--muted-foreground)]">{leftIcon}</span>
        )}
        <input
          ref={ref}
          className={`w-full rounded-lg border border-[var(--border)] bg-[var(--surface)] px-3 py-2 pl-${
            leftIcon ? 10 : 3
          } text-sm text-[var(--foreground)] placeholder-[var(--muted-foreground)] shadow-sm outline-none focus:border-[var(--brand)] focus:ring-2 focus:ring-[var(--brand)] ${className}`}
          {...props}
        />
      </div>
    );
  }
);
Input.displayName = "Input";

export function Label({ className = "", ...props }: React.LabelHTMLAttributes<HTMLLabelElement>) {
  return <label className={`block text-sm font-medium text-[var(--muted-foreground)] mb-1 ${className}`} {...props} />;
}

export function Card({ className = "", ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={`rounded-2xl border border-[var(--border)] bg-[var(--surface)] shadow-lg ${className}`}
      {...props}
    />
  );
}

export function CardHeader({ className = "", ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return <div className={`p-6 pb-3 ${className}`} {...props} />;
}

export function CardContent({ className = "", ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return <div className={`p-6 pt-0 ${className}`} {...props} />;
}

export function Spinner({ className = "w-4 h-4 text-white" }) {
  return (
    <svg className={`animate-spin ${className}`} viewBox="0 0 24 24" fill="none" aria-hidden>
      <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
      <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v4a4 4 0 00-4 4H4z" />
    </svg>
  );
}

export function Alert({ children, variant = "error" as const }: { children: React.ReactNode; variant?: "error" | "success" }) {
  const styles =
    variant === "success"
      ? "bg-green-50 text-green-800 border-green-200"
      : "bg-red-50 text-red-800 border-red-200";
  
  return (
    <div className={`w-full rounded-lg border px-4 py-3 text-sm font-medium ${styles}`}>
      {children}
    </div>
  );
}

export function GradientBar({ className = "h-1 rounded-t-2xl" }) {
  return <div className={`w-full bg-[var(--accent-gradient)] ${className}`} />;
}

export function PageSection({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="container-page py-6 sm:py-8">
      <h2 className="text-xl sm:text-2xl font-bold mb-4 sm:mb-6">{title}</h2>
      {children}
    </section>
  );
}

export function Badge({ children, color = "slate" as const }: { children: React.ReactNode; color?: "slate" | "blue" | "purple" | "rose" | "emerald" | "amber" }) {
  const colors: Record<string, string> = {
    slate: "bg-[#eef2f7] text-[#334155]",
    blue: "bg-[#e0ecff] text-[#1d4ed8]",
    purple: "bg-[#efe7ff] text-[#6d28d9]",
    rose: "bg-[#ffe4e6] text-[#be123c]",
    emerald: "bg-[#dcfce7] text-[#047857]",
    amber: "bg-[#fef3c7] text-[#b45309]",
  };
  return <span className={`inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium ${colors[color]}`}>{children}</span>;
} 