"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { useAuth } from "../../contexts/AuthContext";
import { Button, Card, CardContent, CardHeader, Input, Label, Alert, Spinner } from "../../components/UI";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE || "http://127.0.0.1:8000";

export default function RegisterPage() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const router = useRouter();
  const { login } = useAuth();

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    setError("");

    if (password !== confirmPassword) {
      setError("Passwords do not match");
      setLoading(false);
      return;
    }

    if (password.length < 6) {
      setError("Password must be at least 6 characters long");
      setLoading(false);
      return;
    }

    try {
      const response = await fetch(`${API_BASE}/auth/register`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ email, password }),
      });

      if (response.ok) {
        const loginResponse = await fetch(`${API_BASE}/auth/login`, {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
          },
          body: JSON.stringify({ email, password }),
        });

        if (loginResponse.ok) {
          const data = await loginResponse.json();
          login(data.access_token);
          router.push("/");
        } else {
          const errData = await loginResponse.json().catch(() => ({} as any));
          setError(errData.detail || "Registration successful, but login failed. Please try logging in.");
        }
      } else {
        const errorData = await response.json().catch(() => ({} as any));
        setError(errorData.detail || "Registration failed");
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
            Create your <span className="text-[var(--brand)]">Sava</span> account
          </h1>
          <p className="mt-4 text-[var(--muted-foreground)] max-w-md">
            Start saving bookmarks from YouTube, Instagram, TikTok, Twitter and the web.
          </p>
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
                <div className="text-sm text-[var(--muted-foreground)]">Create a new account</div>
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
                  onChange={(e) => setEmail(e.target.value)}
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
                  onChange={(e) => setPassword(e.target.value)}
                  placeholder="At least 6 characters"
                  required
                />
              </div>

              <div>
                <Label htmlFor="confirm">Confirm Password</Label>
                <Input
                  id="confirm"
                  type="password"
                  value={confirmPassword}
                  onChange={(e) => setConfirmPassword(e.target.value)}
                  placeholder="Re-enter your password"
                  required
                />
              </div>

              <Button type="submit" disabled={loading} className="w-full">
                {loading ? (
                  <span className="inline-flex items-center gap-2"><Spinner /> Creating account...</span>
                ) : (
                  "Register"
                )}
              </Button>
            </form>

            <p className="text-center mt-4 text-sm text-[var(--muted-foreground)]">
              Already have an account?{" "}
              <Link href="/auth/login" className="link hover:underline">Login</Link>
            </p>
          </CardContent>
        </Card>
      </div>
    </main>
  );
}
