"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { useAuth } from "../../contexts/AuthContext";
import { Button, Card, CardContent, CardHeader, Input, Label, Alert, Spinner } from "../../components/UI";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE || "http://127.0.0.1:8000";

export default function LoginPage() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const router = useRouter();
  const { login } = useAuth();

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    setError("");

    try {
      const response = await fetch(`${API_BASE}/auth/login`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ email, password }),
      });

      if (response.ok) {
        const data = await response.json();
        login(data.access_token);
        router.push("/");
      } else {
        const errorData = await response.json().catch(() => ({} as any));
        if (response.status === 404) {
          setError("Email not found");
        } else if (response.status === 401) {
          setError("Incorrect password");
        } else {
          setError(errorData.detail || "Login failed");
        }
      }
    } catch (err) {
      setError("Network error. Please try again.");
    } finally {
      setLoading(false);
    }
  };

  return (
    <main className="min-h-screen flex items-center justify-center p-4">
      <div className="container-page grid grid-cols-1 md:grid-cols-2 gap-8 items-center">
        <div className="hidden md:block">
          <h1 className="text-4xl md:text-5xl font-extrabold leading-tight">
            Welcome back to <span className="text-[var(--brand)]">Sava</span>
          </h1>
          <p className="mt-4 text-[var(--muted-foreground)] max-w-md">
            Save links from anywhere on the web and access them across all your devices.
          </p>
          <div className="mt-8 grid grid-cols-2 gap-4 text-sm text-[var(--muted-foreground)]">
            <div className="card-elevated p-4">
              <div className="font-semibold text-[var(--foreground)] mb-1">Fast</div>
              Add bookmarks in seconds with a clean, simple UI.
            </div>
            <div className="card-elevated p-4">
              <div className="font-semibold text-[var(--foreground)] mb-1">Organized</div>
              Filter by platform and search instantly.
            </div>
          </div>
        </div>

        <Card className="w-full max-w-md mx-auto">
          <CardHeader>
            <div className="flex items-center gap-4">
              <img 
                src="/savaFav.png" 
                alt="Sava Logo" 
                className="h-14 w-14 object-contain"
              />
              <div>
                <div className="text-xl font-bold">Sava</div>
                <div className="text-sm text-[var(--muted-foreground)]">Login to your account</div>
              </div>
            </div>
          </CardHeader>
          <CardContent>
            {error && <Alert>{error}</Alert>}
            <form onSubmit={handleSubmit} className="space-y-4 mt-4">
              <div>
                <Label htmlFor="email">Email</Label>
                <Input
                  id="email"
                  type="email"
                  value={email}
                  onChange={(e) => {
                    setEmail(e.target.value);
                    if (error) setError(""); 
                  }}
                  placeholder="you@example.com"
                  required
                />
              </div>

              <div>
                <Label htmlFor="password">Password</Label>
                <Input
                  id="password"
                  type="password"
                  value={password}
                  onChange={(e) => {
                    setPassword(e.target.value);
                    if (error) setError("");
                  }}
                  placeholder=""
                  required
                />
              </div>

              <Button type="submit" disabled={loading} className="w-full">
                {loading ? (
                  <span className="inline-flex items-center gap-2"><Spinner /> Logging in...</span>
                ) : (
                  "Login"
                )}
              </Button>
            </form>

            <p className="text-center mt-4 text-sm text-[var(--muted-foreground)]">
              Don't have an account?{" "}
              <Link href="/auth/register" className="link hover:underline">Register</Link>
            </p>
          </CardContent>
        </Card>
      </div>
    </main>
  );
}
