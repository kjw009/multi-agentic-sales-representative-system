"use client";

import { Suspense, useEffect, useRef, useState, useCallback } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { api, PricingResult } from "@/lib/api";

interface Message {
  role: "user" | "assistant";
  content: string;
  needsImage?: boolean;
  imageUrl?: string;
}

const POLL_INTERVAL_MS = 3000;
const MAX_POLL_ATTEMPTS = 40; // 40 × 3s = 2 min max wait

function fmt(n: number) {
  return n.toLocaleString("en-US", { style: "currency", currency: "USD" });
}

function ConfidenceBar({ score }: { score: number }) {
  const pct = Math.round(score * 100);
  const color =
    pct >= 70 ? "bg-emerald-500" : pct >= 40 ? "bg-amber-400" : "bg-rose-400";
  return (
    <div className="space-y-1">
      <div className="flex justify-between text-xs text-gray-500">
        <span>Confidence</span>
        <span>{pct}%</span>
      </div>
      <div className="h-1.5 w-full rounded-full bg-gray-100">
        <div className={`h-1.5 rounded-full ${color}`} style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}

function PricingPanel({ pricing }: { pricing: PricingResult }) {
  const hasCI = pricing.price_low > 0 && pricing.price_high > 0;
  return (
    <div className="rounded-2xl border border-gray-200 bg-white p-5 space-y-4 shadow-sm">
      {/* Headline price */}
      <div>
        <p className="text-xs font-medium uppercase tracking-wide text-gray-400 mb-1">
          Recommended price
        </p>
        <p className="text-3xl font-semibold text-gray-900">
          {fmt(pricing.recommended_price)}
        </p>
        {hasCI && (
          <p className="text-sm text-gray-500 mt-0.5">
            Range&nbsp;
            <span className="text-gray-700 font-medium">{fmt(pricing.price_low)}</span>
            &nbsp;–&nbsp;
            <span className="text-gray-700 font-medium">{fmt(pricing.price_high)}</span>
          </p>
        )}
      </div>

      <ConfidenceBar score={pricing.confidence_score} />

      {/* Floor price */}
      <div className="flex justify-between text-sm">
        <span className="text-gray-500">Walk-away floor</span>
        <span className="font-medium text-gray-700">
          {fmt(pricing.min_acceptable_price)}
        </span>
      </div>

      {/* Comparables */}
      {pricing.comparables.length > 0 && (
        <div>
          <p className="text-xs font-medium uppercase tracking-wide text-gray-400 mb-2">
            Comparables ({pricing.comparables.length})
          </p>
          <div className="space-y-2 max-h-56 overflow-y-auto pr-1">
            {pricing.comparables.map((c) => (
              <a
                key={c.item_id}
                href={c.listing_url}
                target="_blank"
                rel="noreferrer"
                className="flex items-start justify-between gap-3 rounded-xl border border-gray-100 px-3 py-2 hover:bg-gray-50 transition-colors group"
              >
                <div className="min-w-0">
                  <p className="text-xs text-gray-700 truncate group-hover:text-gray-900">
                    {c.title}
                  </p>
                  <p className="text-xs text-gray-400 mt-0.5">{c.condition}</p>
                </div>
                <p className="text-sm font-medium text-gray-900 shrink-0">
                  {fmt(c.price)}
                </p>
              </a>
            ))}
          </div>
        </div>
      )}

      {pricing.comparables.length === 0 && (
        <p className="text-xs text-gray-400 italic">
          No live comparables — price based on model prediction only.
        </p>
      )}
    </div>
  );
}

function PricingSpinner() {
  return (
    <div className="rounded-2xl border border-gray-200 bg-white p-5 space-y-3 shadow-sm">
      <p className="text-xs font-medium uppercase tracking-wide text-gray-400">
        Pricing your item…
      </p>
      <div className="flex items-center gap-2">
        <div className="h-2 w-2 rounded-full bg-gray-300 animate-bounce [animation-delay:0ms]" />
        <div className="h-2 w-2 rounded-full bg-gray-300 animate-bounce [animation-delay:150ms]" />
        <div className="h-2 w-2 rounded-full bg-gray-300 animate-bounce [animation-delay:300ms]" />
      </div>
      <p className="text-xs text-gray-400">Searching eBay comparables…</p>
    </div>
  );
}

/* ── Toast notification ────────────────────────────────────────────── */

function Toast({ message, type, onClose }: { message: string; type: "success" | "error" | "info"; onClose: () => void }) {
  const bg = type === "success" ? "bg-emerald-600" : type === "error" ? "bg-rose-600" : "bg-blue-600";

  useEffect(() => {
    const t = setTimeout(onClose, 5000);
    return () => clearTimeout(t);
  }, [onClose]);

  return (
    <div className={`fixed top-4 right-4 z-50 ${bg} text-white rounded-xl px-5 py-3 shadow-lg flex items-center gap-3 animate-[slideIn_0.3s_ease-out]`}>
      <span className="text-sm font-medium">{message}</span>
      <button onClick={onClose} className="text-white/70 hover:text-white text-lg leading-none">&times;</button>
    </div>
  );
}

/* ── Main page ─────────────────────────────────────────────────────── */

function ChatPageInner() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const [messages, setMessages] = useState<Message[]>([]);
  const [itemId, setItemId] = useState<string | null>(null);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [connectingEbay, setConnectingEbay] = useState(false);

  // eBay connection state
  const [ebayConnected, setEbayConnected] = useState(false);
  const [toast, setToast] = useState<{ message: string; type: "success" | "error" | "info" } | null>(null);

  // Pricing state
  const [pricingResult, setPricingResult] = useState<PricingResult | null>(null);
  const [pricingPending, setPricingPending] = useState(false);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const pollAttempts = useRef(0);

  const bottomRef = useRef<HTMLDivElement>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!localStorage.getItem("token")) {
      router.push("/login");
      return;
    }
    setMessages([{
      role: "assistant",
      content: "Hi! I'm your AI selling assistant. Tell me about an item you'd like to sell — what is it?",
    }]);

    // Check eBay connection status on mount
    api.ebayStatus()
      .then((res) => setEbayConnected(res.connected))
      .catch(() => {}); // Silently fail — not critical

    // Handle eBay OAuth callback query params
    const ebayParam = searchParams.get("ebay");
    if (ebayParam === "connected") {
      setEbayConnected(true);
      setToast({ message: "eBay account connected successfully!", type: "success" });
      // Clean up the URL without reloading
      window.history.replaceState({}, "", "/chat");
    } else if (ebayParam === "declined") {
      setToast({ message: "eBay connection was declined. You can try again anytime.", type: "info" });
      window.history.replaceState({}, "", "/chat");
    } else if (ebayParam === "error") {
      setToast({ message: "Something went wrong connecting to eBay. Please try again.", type: "error" });
      window.history.replaceState({}, "", "/chat");
    }
  }, [router, searchParams]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  // Cleanup polling on unmount
  useEffect(() => () => { if (pollRef.current) clearInterval(pollRef.current); }, []);

  const startPolling = useCallback((id: string) => {
    if (pollRef.current) clearInterval(pollRef.current);
    pollAttempts.current = 0;
    setPricingPending(true);

    pollRef.current = setInterval(async () => {
      pollAttempts.current += 1;
      if (pollAttempts.current > MAX_POLL_ATTEMPTS) {
        clearInterval(pollRef.current!);
        setPricingPending(false);
        return;
      }
      try {
        const result = await api.getPricing(id);
        if (result) {
          clearInterval(pollRef.current!);
          setPricingPending(false);
          setPricingResult(result);
        }
      } catch {
        // swallow — keep polling
      }
    }, POLL_INTERVAL_MS);
  }, []);

  async function handleConnectEbay() {
    setConnectingEbay(true);
    try {
      const { authorization_url } = await api.ebayConnect();
      window.location.href = authorization_url;
    } catch (err: unknown) {
      setToast({ message: err instanceof Error ? err.message : "Failed to start eBay connection", type: "error" });
      setConnectingEbay(false);
    }
  }

  async function sendMessage(e: React.SyntheticEvent) {
    e.preventDefault();
    const content = input.trim();
    if (!content || loading) return;

    setInput("");
    setMessages((prev) => [...prev, { role: "user", content }]);
    setLoading(true);

    try {
      const reply = await api.sendMessage(content, itemId);
      if (reply.item_id) setItemId(reply.item_id);
      setMessages((prev) => [
        ...prev,
        { role: "assistant", content: reply.content, needsImage: reply.needs_image },
      ]);
      // Intake just finished — start polling for pricing
      if (reply.intake_complete && reply.item_id) {
        startPolling(reply.item_id);
      }
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Error";
      setMessages((prev) => [...prev, { role: "assistant", content: `⚠ ${msg}` }]);
    } finally {
      setLoading(false);
    }
  }

  async function handleFileChange(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file || !itemId) return;

    setUploading(true);
    const localUrl = URL.createObjectURL(file);
    setMessages((prev) => [...prev, { role: "user", content: "", imageUrl: localUrl }]);

    try {
      await api.uploadImage(file, itemId);
      const reply = await api.sendMessage("I've uploaded the image.", itemId);
      if (reply.item_id) setItemId(reply.item_id);
      setMessages((prev) => [
        ...prev,
        { role: "assistant", content: reply.content, needsImage: reply.needs_image },
      ]);
      if (reply.intake_complete && reply.item_id) {
        startPolling(reply.item_id);
      }
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Upload failed";
      setMessages((prev) => [...prev, { role: "assistant", content: `⚠ ${msg}` }]);
    } finally {
      setUploading(false);
      e.target.value = "";
    }
  }

  function logout() {
    localStorage.clear();
    router.push("/login");
  }

  const showPricingPanel = pricingResult !== null || pricingPending;

  return (
    <div className="flex h-screen bg-gray-50">
      {/* Toast notification */}
      {toast && <Toast message={toast.message} type={toast.type} onClose={() => setToast(null)} />}

      {/* Sidebar */}
      <aside className="w-56 bg-white border-r border-gray-200 flex flex-col p-4 gap-3 shrink-0">
        <p className="font-semibold text-sm">SalesRep</p>
        <div className="flex-1" />

        {/* eBay connection button / status */}
        {ebayConnected ? (
          <div className="w-full text-sm bg-emerald-50 text-emerald-700 border border-emerald-200 rounded-lg px-3 py-2 flex items-center gap-2">
            <svg className="w-4 h-4 shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2.5}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
            </svg>
            <span>eBay Connected</span>
          </div>
        ) : (
          <button
            onClick={handleConnectEbay}
            disabled={connectingEbay}
            className="w-full text-sm bg-blue-600 text-white rounded-lg px-3 py-2 hover:bg-blue-700 disabled:opacity-50 transition-colors"
          >
            {connectingEbay ? "Redirecting…" : "Connect eBay"}
          </button>
        )}

        <button
          onClick={logout}
          className="w-full text-left text-sm text-gray-400 hover:text-gray-700 px-1 transition-colors"
        >
          Log out
        </button>
      </aside>

      {/* Chat */}
      <main className="flex-1 flex flex-col min-w-0">
        <div className="flex-1 overflow-y-auto px-6 py-6 space-y-4">
          {messages.map((m, i) => (
            <div key={i} className={`flex ${m.role === "user" ? "justify-end" : "justify-start"}`}>
              <div className="max-w-lg space-y-2">
                {m.imageUrl && (
                  <div className="flex justify-end">
                    {/* eslint-disable-next-line @next/next/no-img-element */}
                    <img
                      src={m.imageUrl}
                      alt="uploaded"
                      className="max-h-48 rounded-xl border border-gray-200 object-cover"
                    />
                  </div>
                )}
                {m.content && (
                  <div
                    className={`rounded-2xl px-4 py-2.5 text-sm leading-relaxed ${
                      m.role === "user"
                        ? "bg-gray-900 text-white rounded-br-sm"
                        : "bg-white border border-gray-200 text-gray-800 rounded-bl-sm"
                    }`}
                  >
                    {m.content}
                  </div>
                )}
                {m.needsImage && (
                  <div className="flex justify-start">
                    <button
                      onClick={() => fileRef.current?.click()}
                      disabled={uploading || !itemId}
                      className="flex items-center gap-2 text-sm bg-gray-100 hover:bg-gray-200 border border-gray-200 rounded-xl px-4 py-2 disabled:opacity-50 transition-colors"
                    >
                      {uploading ? "Uploading…" : "📷 Upload photo"}
                    </button>
                  </div>
                )}
              </div>
            </div>
          ))}

          {(loading || uploading) && (
            <div className="flex justify-start">
              <div className="bg-white border border-gray-200 rounded-2xl rounded-bl-sm px-4 py-2.5 text-sm text-gray-400">
                …
              </div>
            </div>
          )}
          <div ref={bottomRef} />
        </div>

        <input
          ref={fileRef}
          type="file"
          accept="image/jpeg,image/png,image/gif,image/webp"
          className="hidden"
          onChange={handleFileChange}
        />

        <form
          onSubmit={sendMessage}
          className="border-t border-gray-200 bg-white px-6 py-4 flex gap-3"
        >
          <input
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder="Describe what you want to sell…"
            className="flex-1 border border-gray-200 rounded-xl px-4 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-gray-900"
          />
          <button
            type="submit"
            disabled={loading || uploading || !input.trim()}
            className="bg-gray-900 text-white rounded-xl px-4 py-2 text-sm font-medium hover:bg-gray-700 disabled:opacity-40 transition-colors"
          >
            Send
          </button>
        </form>
      </main>

      {/* Pricing panel — slides in once intake completes */}
      {showPricingPanel && (
        <aside className="w-80 shrink-0 bg-gray-50 border-l border-gray-200 p-5 overflow-y-auto">
          <p className="text-xs font-medium uppercase tracking-wide text-gray-400 mb-4">
            Pricing
          </p>
          {pricingPending && !pricingResult && <PricingSpinner />}
          {pricingResult && <PricingPanel pricing={pricingResult} />}
        </aside>
      )}
    </div>
  );
}

export default function ChatPage() {
  return (
    <Suspense fallback={<div className="flex h-screen items-center justify-center bg-gray-50 text-gray-400">Loading…</div>}>
      <ChatPageInner />
    </Suspense>
  );
}
