"use client";

import { Suspense, useEffect, useRef, useState, useCallback } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { Camera, Send } from "lucide-react";
import { api, PricingResult, ListingStatus } from "@/lib/api";
import { AppShell } from "@/components/AppShell";
import { Toast, ToastType } from "@/components/Toast";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";

interface Message {
  role: "user" | "assistant";
  content: string;
  needsImage?: boolean;
  imageUrl?: string;
}

const POLL_INTERVAL_MS = 3000;
const MAX_POLL_ATTEMPTS = 40;

function fmt(n: number) {
  return n.toLocaleString("en-GB", { style: "currency", currency: "GBP" });
}

/* ── Pricing panel ──────────────────────────────────────────────────── */

function ConfidenceBar({ score }: { score: number }) {
  const pct = Math.round(score * 100);
  const color = pct >= 70 ? "bg-emerald-500" : pct >= 40 ? "bg-amber-400" : "bg-rose-400";
  return (
    <div className="space-y-1">
      <div className="flex justify-between text-xs text-muted-foreground">
        <span>Confidence</span>
        <span>{pct}%</span>
      </div>
      <div className="h-1.5 w-full rounded-full bg-muted">
        <div className={`h-1.5 rounded-full ${color} transition-all`} style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}

function PricingPanel({ pricing }: { pricing: PricingResult }) {
  const hasCI = pricing.price_low > 0 && pricing.price_high > 0;
  return (
    <Card>
      <CardContent className="p-5 space-y-4">
        <div>
          <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground mb-1">
            Recommended price
          </p>
          <p className="text-3xl font-semibold">{fmt(pricing.recommended_price)}</p>
          {hasCI && (
            <p className="text-sm text-muted-foreground mt-0.5">
              Range{" "}
              <span className="text-foreground font-medium">{fmt(pricing.price_low)}</span>
              {" – "}
              <span className="text-foreground font-medium">{fmt(pricing.price_high)}</span>
            </p>
          )}
        </div>

        <ConfidenceBar score={pricing.confidence_score} />

        <div className="flex flex-wrap justify-between gap-2 text-sm">
          <span className="text-muted-foreground">Walk-away floor</span>
          <span className="font-medium">{fmt(pricing.min_acceptable_price)}</span>
        </div>

        {pricing.comparables.length > 0 && (
          <div>
            <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground mb-2">
              Comparables ({pricing.comparables.length})
            </p>
            <div className="space-y-1.5 max-h-56 overflow-y-auto pr-1">
              {pricing.comparables.map((c) => (
                <a
                  key={c.item_id}
                  href={c.listing_url}
                  target="_blank"
                  rel="noreferrer"
                  className="flex items-start justify-between gap-3 rounded-xl border border-border px-3 py-2 hover:bg-accent/60 transition-colors group"
                >
                  <div className="min-w-0">
                    <p className="text-xs truncate group-hover:text-foreground text-muted-foreground">{c.title}</p>
                    <p className="text-xs text-muted-foreground/60 mt-0.5">{c.condition}</p>
                  </div>
                  <p className="text-sm font-medium shrink-0">{fmt(c.price)}</p>
                </a>
              ))}
            </div>
          </div>
        )}

        {pricing.comparables.length === 0 && (
          <p className="text-xs text-muted-foreground italic">
            No live comparables — price based on model prediction only.
          </p>
        )}
      </CardContent>
    </Card>
  );
}

function PricingPanelSkeleton() {
  return (
    <Card>
      <CardContent className="p-5 space-y-4">
        <div className="space-y-2">
          <Skeleton className="h-3 w-32" />
          <Skeleton className="h-8 w-24" />
        </div>
        <Skeleton className="h-2 w-full" />
        <div className="flex justify-between">
          <Skeleton className="h-4 w-24" />
          <Skeleton className="h-4 w-16" />
        </div>
        <p className="text-xs text-muted-foreground">Searching eBay comparables…</p>
      </CardContent>
    </Card>
  );
}

/* ── Listing status panel ───────────────────────────────────────────── */

function ListingStatusPanel({ listing }: { listing: ListingStatus }) {
  const badgeVariant: Record<string, "success" | "warning" | "destructive" | "info" | "secondary"> = {
    live: "success",
    publishing: "warning",
    error: "destructive",
    needs_specifics: "info",
    ended: "secondary",
  };
  const labels: Record<string, string> = {
    live: "Live on eBay",
    publishing: "Publishing…",
    error: "Publish failed",
    needs_specifics: listing.required_specifics?.length
      ? `eBay needs: ${listing.required_specifics.join(", ")}`
      : "More info needed",
    ended: "Listing ended",
  };

  return (
    <Card>
      <CardContent className="p-4 space-y-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <p className="text-sm font-medium">{labels[listing.status] ?? listing.status}</p>
          <Badge variant={badgeVariant[listing.status] ?? "secondary"}>
            {listing.status}
          </Badge>
        </div>
        {listing.posted_price != null && (
          <p className="text-xs text-muted-foreground">
            Listed at <span className="font-medium text-foreground">{fmt(listing.posted_price)}</span>
          </p>
        )}
        {listing.url && (
          <a
            href={listing.url}
            target="_blank"
            rel="noreferrer"
            className="inline-flex items-center gap-1.5 text-xs font-medium text-primary hover:underline underline-offset-2"
          >
            View on eBay →
          </a>
        )}
      </CardContent>
    </Card>
  );
}

/* ── Main page ──────────────────────────────────────────────────────── */

function ChatPageInner() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const [messages, setMessages] = useState<Message[]>([]);
  const [itemId, setItemId] = useState<string | null>(null);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [connectingEbay, setConnectingEbay] = useState(false);
  const [ebayConnected, setEbayConnected] = useState(false);
  const [toast, setToast] = useState<{ message: string; type: ToastType } | null>(null);
  const [pricingResult, setPricingResult] = useState<PricingResult | null>(null);
  const [pricingPending, setPricingPending] = useState(false);
  const [listingStatus, setListingStatus] = useState<ListingStatus | null>(null);
  const [listingPending, setListingPending] = useState(false);

  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const pollAttempts = useRef(0);
  const bottomRef = useRef<HTMLDivElement>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!localStorage.getItem("token")) { router.push("/login"); return; }
    setMessages([{
      role: "assistant",
      content: "Hi! I'm your AI selling assistant. Tell me about an item you'd like to sell — what is it?",
    }]);
    api.ebayStatus().then((r) => setEbayConnected(r.connected)).catch(() => {});
    const p = searchParams.get("ebay");
    if (p === "connected") {
      setEbayConnected(true);
      setToast({ message: "eBay account connected!", type: "success" });
      window.history.replaceState({}, "", "/chat");
    } else if (p === "declined") {
      setToast({ message: "eBay connection declined.", type: "info" });
      window.history.replaceState({}, "", "/chat");
    } else if (p === "error") {
      setToast({ message: "eBay connection error. Try again.", type: "error" });
      window.history.replaceState({}, "", "/chat");
    }
  }, [router, searchParams]);

  useEffect(() => { bottomRef.current?.scrollIntoView({ behavior: "smooth" }); }, [messages]);
  useEffect(() => () => { if (pollRef.current) clearInterval(pollRef.current); }, []);

  const startPolling = useCallback((id: string) => {
    if (pollRef.current) clearInterval(pollRef.current);
    pollAttempts.current = 0;
    setPricingPending(true);

    pollRef.current = setInterval(async () => {
      pollAttempts.current++;
      if (pollAttempts.current > MAX_POLL_ATTEMPTS) {
        clearInterval(pollRef.current!);
        setPricingPending(false);
        setListingPending(false);
        return;
      }
      try {
        if (!pricingResult) {
          const result = await api.getPricing(id);
          if (result) {
            setPricingPending(false);
            setPricingResult(result);
            setListingPending(true);
          }
        }
        const ls = await api.getListingStatus(id);
        if (ls) {
          setListingStatus(ls);
          if (ls.status === "needs_specifics") {
            const next = ls.required_specifics?.[0];
            if (next) {
              const promptText = `To finish publishing on eBay I just need the ${next} — could you tell me?`;
              setMessages((prev) =>
                prev.length > 0 && prev[prev.length - 1].content === promptText
                  ? prev
                  : [...prev, { role: "assistant", content: promptText }]
              );
            }
          }
          if (["live","error","ended","needs_specifics"].includes(ls.status)) {
            setListingPending(false);
            if (!pricingPending) clearInterval(pollRef.current!);
          }
        }
      } catch { /* keep polling */ }
    }, POLL_INTERVAL_MS);
  }, [pricingResult, pricingPending]);

  async function handleConnectEbay() {
    setConnectingEbay(true);
    try {
      const { authorization_url } = await api.ebayConnect();
      window.location.href = authorization_url;
    } catch (err: unknown) {
      setToast({ message: err instanceof Error ? err.message : "Failed", type: "error" });
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
      setMessages((prev) => [...prev, { role: "assistant", content: reply.content, needsImage: reply.needs_image }]);
      if (reply.intake_complete && reply.item_id) startPolling(reply.item_id);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Error";
      setMessages((prev) => [...prev, { role: "assistant", content: `⚠ ${msg}` }]);
    } finally {
      setLoading(false);
    }
  }

  async function handleFileChange(e: React.ChangeEvent<HTMLInputElement>) {
    const files = e.target.files;
    if (!files || files.length === 0 || !itemId) return;
    setUploading(true);
    const fileList = Array.from(files);
    setMessages((prev) => [
      ...prev,
      ...fileList.map((f) => ({
        role: "user" as const,
        content: "",
        imageUrl: URL.createObjectURL(f),
      })),
    ]);
    try {
      // Upload sequentially so each image gets a stable, increasing position.
      for (const file of fileList) {
        await api.uploadImage(file, itemId);
      }
      const noun = fileList.length === 1 ? "a photo" : `${fileList.length} photos`;
      const reply = await api.sendMessage(`I've uploaded ${noun}.`, itemId);
      if (reply.item_id) setItemId(reply.item_id);
      setMessages((prev) => [...prev, { role: "assistant", content: reply.content, needsImage: reply.needs_image }]);
      if (reply.intake_complete && reply.item_id) startPolling(reply.item_id);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Upload failed";
      setMessages((prev) => [...prev, { role: "assistant", content: `⚠ ${msg}` }]);
    } finally {
      setUploading(false);
      e.target.value = "";
    }
  }

  const showPricingPanel = pricingResult !== null || pricingPending;
  const showListingPanel = listingStatus !== null || listingPending;

  const panel = (showPricingPanel || showListingPanel) ? (
    <div className="space-y-6">
      {showPricingPanel && (
        <div>
          <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground mb-3">Pricing</p>
          {pricingPending && !pricingResult ? <PricingPanelSkeleton /> : null}
          {pricingResult ? <PricingPanel pricing={pricingResult} /> : null}
        </div>
      )}
      {showListingPanel && (
        <div>
          <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground mb-3">Listing</p>
          {listingPending && !listingStatus ? (
            <Card><CardContent className="p-4"><Skeleton className="h-4 w-40" /></CardContent></Card>
          ) : null}
          {listingStatus ? <ListingStatusPanel listing={listingStatus} /> : null}
        </div>
      )}
    </div>
  ) : undefined;

  return (
    <>
      {toast && <Toast message={toast.message} type={toast.type} onClose={() => setToast(null)} />}
      <AppShell
        panel={panel}
        ebayConnected={ebayConnected}
        onConnectEbay={handleConnectEbay}
        connectingEbay={connectingEbay}
      >
        {/* Messages */}
        <div className="flex h-full min-h-0 flex-col">
          <div className="flex-1 overflow-y-auto px-4 py-4 sm:px-6 sm:py-6 space-y-4">
            {messages.map((m, i) => (
              <div key={i} className={`flex ${m.role === "user" ? "justify-end" : "justify-start"}`}>
                <div className="max-w-[min(32rem,88vw)] space-y-2 sm:max-w-lg">
                  {m.imageUrl && (
                    <div className="flex justify-end">
                      {/* eslint-disable-next-line @next/next/no-img-element */}
                      <img src={m.imageUrl} alt="upload" className="max-h-48 max-w-full rounded-xl border border-border object-cover" />
                    </div>
                  )}
                  {m.content && (
                    <div className={`rounded-2xl px-4 py-2.5 text-sm leading-relaxed ${
                      m.role === "user"
                        ? "bg-primary text-primary-foreground rounded-br-sm"
                        : "bg-card border border-border text-foreground rounded-bl-sm"
                    }`}>
                      {m.content}
                    </div>
                  )}
                  {m.needsImage && (
                    <div className="flex justify-start">
                      <Button
                        variant="outline"
                        size="sm"
                        onClick={() => fileRef.current?.click()}
                        disabled={uploading || !itemId}
                      >
                        <Camera size={14} />
                        {uploading ? "Uploading…" : "Upload photos"}
                      </Button>
                    </div>
                  )}
                </div>
              </div>
            ))}
            {(loading || uploading) && (
              <div className="flex justify-start">
                <div className="bg-card border border-border rounded-2xl rounded-bl-sm px-4 py-2.5 text-sm text-muted-foreground">…</div>
              </div>
            )}
            {panel && (
              <div className="xl:hidden pt-2">
                {panel}
              </div>
            )}
            <div ref={bottomRef} />
          </div>

          <input ref={fileRef} type="file" accept="image/jpeg,image/png,image/gif,image/webp" multiple className="hidden" onChange={handleFileChange} />

          <form onSubmit={sendMessage} className="border-t border-border bg-card px-3 py-3 sm:px-6 sm:py-4 flex gap-2 sm:gap-3">
            <Input
              value={input}
              onChange={(e) => setInput(e.target.value)}
              placeholder="Describe what you want to sell…"
              className="flex-1 h-10"
            />
            <Button type="submit" disabled={loading || uploading || !input.trim()} size="sm">
              <Send size={14} />
              <span className="hidden sm:inline">Send</span>
            </Button>
          </form>
        </div>
      </AppShell>
    </>
  );
}

export default function ChatPage() {
  return (
    <Suspense fallback={<div className="flex h-screen items-center justify-center text-muted-foreground">Loading…</div>}>
      <ChatPageInner />
    </Suspense>
  );
}
