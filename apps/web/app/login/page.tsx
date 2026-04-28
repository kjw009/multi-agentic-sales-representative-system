"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { api } from "@/lib/api";

/**
 * Login/Signup page component.
 *
 * Provides a form for users to log in or sign up, with tab switching
 * between login and signup modes. Stores auth token on success and redirects to chat.
 */
export default function LoginPage() {
  const router = useRouter();
  // Current active tab: login or signup
  const [tab, setTab] = useState<"login" | "signup">("login");
  // Email input value
  const [email, setEmail] = useState("");
  // Password input value
  const [password, setPassword] = useState("");
  // Error message to display
  const [error, setError] = useState("");
  // Loading state during API call
  const [loading, setLoading] = useState(false);

  // Handle form submission for login or signup
  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    setLoading(true);
    try {
      // Call appropriate API based on current tab
      const res = tab === "login"
        ? await api.login(email, password)
        : await api.signup(email, password);
      // Store auth data in localStorage
      localStorage.setItem("token", res.access_token);
      localStorage.setItem("seller_id", res.seller_id);
      // Redirect to chat page
      router.push("/chat");
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Something went wrong");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="min-h-screen flex items-center justify-center">
      {/* Centered login/signup card */}
      <div className="w-full max-w-sm bg-white rounded-2xl shadow-sm border border-gray-200 p-8">
        {/* App branding */}
        <h1 className="text-xl font-semibold mb-6 text-center">SalesRep</h1>

        {/* Tab selector for login/signup */}
        <div className="flex rounded-lg overflow-hidden border border-gray-200 mb-6">
          {(["login", "signup"] as const).map((t) => (
            <button
              key={t}
              onClick={() => { setTab(t); setError(""); }}
              className={`flex-1 py-2 text-sm font-medium transition-colors ${
                tab === t ? "bg-gray-900 text-white" : "bg-white text-gray-500 hover:bg-gray-50"
              }`}
            >
              {t === "login" ? "Log in" : "Sign up"}
            </button>
          ))}
        </div>

        {/* Login/signup form */}
        <form onSubmit={submit} className="space-y-4">
          {/* Email input */}
          <input
            type="email"
            placeholder="Email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            required
            className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-gray-900"
          />
          {/* Password input */}
          <input
            type="password"
            placeholder="Password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
            className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-gray-900"
          />
          {/* Error message display */}
          {error && <p className="text-red-500 text-sm">{error}</p>}
          {/* Submit button */}
          <button
            type="submit"
            disabled={loading}
            className="w-full bg-gray-900 text-white rounded-lg py-2 text-sm font-medium hover:bg-gray-700 disabled:opacity-50 transition-colors"
          >
            {loading ? "…" : tab === "login" ? "Log in" : "Create account"}
          </button>
        </form>
      </div>
    </div>
  );
}
