"use client";

import { useEffect, useState, useCallback } from "react";
import { useRouter } from "next/navigation";
import { api, DraftMessage } from "@/lib/api";
import { AppShell } from "@/components/AppShell";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";

function formatRelativeTime(dateString: string) {
  const diff = Math.floor((Date.now() - new Date(dateString).getTime()) / 1000);
  if (diff < 60) return "just now";
  const m = Math.floor(diff / 60);
  if (m < 60) return `${m} min ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h} hr ago`;
  return `${Math.floor(h / 24)} day${Math.floor(h / 24) > 1 ? "s" : ""} ago`;
}

function DraftItem({ draft, onRemove }: { draft: DraftMessage; onRemove: (id: string) => void }) {
  const [text, setText] = useState(draft.draft_reply || "");
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState("");
  const hasEdits = text !== draft.draft_reply;

  async function act(fn: () => Promise<unknown>) {
    setLoading(true); setErr("");
    try { await fn(); onRemove(draft.message_id); }
    catch (e: unknown) { setErr(e instanceof Error ? e.message : "Action failed"); setLoading(false); }
  }

  return (
    <Card>
      <CardContent className="p-5 space-y-4">
        <div className="flex justify-between items-center text-sm">
          <span className="font-semibold">{draft.buyer_handle}</span>
          <span className="text-muted-foreground text-xs">{formatRelativeTime(draft.received_at)}</span>
        </div>

        {/* Buyer message */}
        <div className="bg-muted rounded-2xl rounded-tl-sm px-4 py-3 text-sm text-foreground">
          {draft.raw_text}
        </div>

        {/* Draft reply editor */}
        <div className="space-y-1.5">
          <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Agent Draft Reply</p>
          <Textarea
            value={text}
            onChange={(e) => setText(e.target.value)}
            disabled={loading}
            placeholder="Type your reply…"
            className="min-h-[100px]"
          />
        </div>

        {err && <p className="text-destructive text-xs">{err}</p>}

        <div className="flex items-center gap-2 justify-end">
          <Button
            variant="ghost"
            size="sm"
            disabled={loading}
            onClick={() => act(() => api.dismissDraft(draft.message_id))}
          >
            Dismiss
          </Button>
          {hasEdits ? (
            <Button
              size="sm"
              disabled={loading || !text.trim()}
              onClick={() => act(() => api.editDraft(draft.message_id, text))}
            >
              Save &amp; Send
            </Button>
          ) : (
            <Button
              size="sm"
              disabled={loading}
              onClick={() => act(() => api.approveDraft(draft.message_id))}
            >
              Approve
            </Button>
          )}
        </div>
      </CardContent>
    </Card>
  );
}

export default function InboxPage() {
  const router = useRouter();
  const [drafts, setDrafts] = useState<DraftMessage[]>([]);
  const [loading, setLoading] = useState(true);

  const fetchDrafts = useCallback(async () => {
    try { setDrafts(await api.getDrafts()); }
    catch { /* ignore */ }
    finally { setLoading(false); }
  }, []);

  useEffect(() => {
    if (!localStorage.getItem("token")) { router.push("/login"); return; }
    fetchDrafts();
    const iv = setInterval(fetchDrafts, 10000);
    return () => clearInterval(iv);
  }, [fetchDrafts, router]);

  const handleRemove = (id: string) => {
    setDrafts((prev) => prev.filter((d) => d.message_id !== id));
    fetchDrafts();
  };

  return (
    <AppShell inboxCount={drafts.length}>
      <div className="px-4 sm:px-8 py-8">
        <div className="max-w-2xl mx-auto space-y-6">
          <div className="pb-4 border-b border-border">
            <h1 className="text-xl font-semibold">Pending Approvals</h1>
            {drafts.length > 0 && (
              <p className="text-sm text-muted-foreground mt-1">
                {drafts.length} draft{drafts.length > 1 ? "s" : ""} waiting for review
              </p>
            )}
          </div>

          {loading && drafts.length === 0 ? (
            <div className="space-y-4">
              {[1, 2].map((n) => (
                <Card key={n}><CardContent className="p-5 space-y-3">
                  <Skeleton className="h-4 w-32" />
                  <Skeleton className="h-16 w-full" />
                  <Skeleton className="h-24 w-full" />
                </CardContent></Card>
              ))}
            </div>
          ) : drafts.length === 0 ? (
            <div className="text-sm text-muted-foreground text-center py-12 border border-dashed border-border rounded-2xl">
              No pending drafts
            </div>
          ) : (
            <div className="space-y-6">
              {drafts.map((d) => <DraftItem key={d.message_id} draft={d} onRemove={handleRemove} />)}
            </div>
          )}
        </div>
      </div>
    </AppShell>
  );
}
