"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { api } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

export default function LoginPage() {
  const router = useRouter();
  const [tab, setTab] = useState<"login" | "signup">("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    setLoading(true);
    try {
      const res =
        tab === "login"
          ? await api.login(email, password)
          : await api.signup(email, password);
      localStorage.setItem("token", res.access_token);
      localStorage.setItem("seller_id", res.seller_id);
      // Route to onboarding if not yet completed, else chat
      const onboarded = await api.getOnboardingStatus().catch(() => true);
      router.push(onboarded ? "/chat" : "/onboarding");
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Something went wrong");
    } finally {
      setLoading(false);
    }
  }

  async function enterDemo() {
    setLoading(true);
    try {
      const res = await api.demoLogin();
      localStorage.setItem("token", res.access_token);
      localStorage.setItem("seller_id", res.seller_id);
      router.push("/chat");
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Demo unavailable");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="min-h-screen flex flex-col items-center justify-center gap-4 px-4 bg-background">
      <Card className="w-full max-w-sm">
        <CardHeader className="pb-2">
          <CardTitle className="text-center text-xl">SalesRep</CardTitle>
          <p className="text-center text-sm text-muted-foreground">
            AI-powered eBay selling assistant
          </p>
        </CardHeader>
        <CardContent className="space-y-4">
          {/* Tab switcher */}
          <div className="flex rounded-xl overflow-hidden border border-border">
            {(["login", "signup"] as const).map((t) => (
              <button
                key={t}
                onClick={() => { setTab(t); setError(""); }}
                className={`flex-1 py-2 text-sm font-medium transition-colors ${
                  tab === t
                    ? "bg-primary text-primary-foreground"
                    : "bg-card text-muted-foreground hover:bg-accent"
                }`}
              >
                {t === "login" ? "Log in" : "Sign up"}
              </button>
            ))}
          </div>

          <form onSubmit={submit} className="space-y-3">
            <Input
              type="email"
              placeholder="Email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              required
            />
            <Input
              type="password"
              placeholder="Password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
            />
            {error && <p className="text-destructive text-sm">{error}</p>}
            <Button type="submit" disabled={loading} className="w-full">
              {loading ? "…" : tab === "login" ? "Log in" : "Create account"}
            </Button>
          </form>

          <div className="relative">
            <div className="absolute inset-0 flex items-center">
              <span className="w-full border-t border-border" />
            </div>
            <div className="relative flex justify-center text-xs uppercase">
              <span className="bg-card px-2 text-muted-foreground">or</span>
            </div>
          </div>

          <Button
            type="button"
            variant="outline"
            disabled={loading}
            className="w-full"
            onClick={enterDemo}
          >
            Try the live demo
          </Button>
        </CardContent>
      </Card>

      <p className="text-xs text-muted-foreground text-center max-w-xs">
        No credit card required for the free tier. eBay connection needed to list items.
      </p>
    </div>
  );
}
