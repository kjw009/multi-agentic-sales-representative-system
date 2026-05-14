"use client";

import { useEffect, useState, useCallback } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { api, DraftMessage } from "@/lib/api";

function formatRelativeTime(dateString: string) {
  const date = new Date(dateString);
  const now = new Date();
  const diffInSeconds = Math.floor((now.getTime() - date.getTime()) / 1000);

  if (diffInSeconds < 60) return "just now";
  const diffInMinutes = Math.floor(diffInSeconds / 60);
  if (diffInMinutes < 60) return `${diffInMinutes} min ago`;
  const diffInHours = Math.floor(diffInMinutes / 60);
  if (diffInHours < 24) return `${diffInHours} hr ago`;
  const diffInDays = Math.floor(diffInHours / 24);
  return `${diffInDays} day${diffInDays > 1 ? "s" : ""} ago`;
}

function DraftItem({
  draft,
  onRemove,
}: {
  draft: DraftMessage;
  onRemove: (id: string) => void;
}) {
  const [text, setText] = useState(draft.draft_reply || "");
  const [loading, setLoading] = useState(false);

  const handleApprove = async () => {
    setLoading(true);
    try {
      await api.approveDraft(draft.message_id);
      onRemove(draft.message_id);
    } catch (err) {
      alert("Failed to approve draft");
      setLoading(false);
    }
  };

  const handleSaveAndSend = async () => {
    if (!text.trim()) return;
    setLoading(true);
    try {
      await api.editDraft(draft.message_id, text);
      onRemove(draft.message_id);
    } catch (err) {
      alert("Failed to send edited draft");
      setLoading(false);
    }
  };

  const handleDismiss = async () => {
    setLoading(true);
    try {
      await api.dismissDraft(draft.message_id);
      onRemove(draft.message_id);
    } catch (err) {
      alert("Failed to dismiss draft");
      setLoading(false);
    }
  };

  const hasEdits = text !== draft.draft_reply;

  return (
    <div className="bg-white rounded-2xl border border-gray-200 p-5 shadow-sm space-y-4">
      <div className="flex justify-between items-center text-sm">
        <span className="font-semibold text-gray-900">{draft.buyer_handle}</span>
        <span className="text-gray-500">{formatRelativeTime(draft.received_at)}</span>
      </div>

      <div className="bg-gray-100 text-gray-800 rounded-2xl rounded-tl-sm px-4 py-3 text-sm">
        {draft.raw_text}
      </div>

      <div className="space-y-2">
        <label className="block text-xs font-medium uppercase tracking-wide text-gray-500">
          Agent Draft Reply
        </label>
        <textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          disabled={loading}
          className="w-full border border-gray-200 rounded-xl px-4 py-3 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 min-h-[100px] resize-y"
          placeholder="Type your reply here..."
        />
      </div>

      <div className="flex items-center gap-3 justify-end pt-2">
        <button
          onClick={handleDismiss}
          disabled={loading}
          className="px-4 py-2 text-sm font-medium text-gray-600 hover:bg-gray-100 rounded-xl disabled:opacity-50 transition-colors"
        >
          Dismiss
        </button>

        {hasEdits ? (
          <button
            onClick={handleSaveAndSend}
            disabled={loading || !text.trim()}
            className="px-4 py-2 text-sm font-medium bg-blue-600 text-white hover:bg-blue-700 rounded-xl disabled:opacity-50 transition-colors"
          >
            Save & Send
          </button>
        ) : (
          <button
            onClick={handleApprove}
            disabled={loading}
            className="px-4 py-2 text-sm font-medium bg-gray-900 text-white hover:bg-gray-800 rounded-xl disabled:opacity-50 transition-colors"
          >
            Approve
          </button>
        )}
      </div>
    </div>
  );
}

export default function InboxPage() {
  const router = useRouter();
  const [drafts, setDrafts] = useState<DraftMessage[]>([]);
  const [loading, setLoading] = useState(true);

  const fetchDrafts = useCallback(async () => {
    try {
      const data = await api.getDrafts();
      setDrafts(data);
    } catch (err) {
      console.error("Failed to fetch drafts", err);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!localStorage.getItem("token")) {
      router.push("/login");
      return;
    }

    fetchDrafts();
    const interval = setInterval(fetchDrafts, 10000);
    return () => clearInterval(interval);
  }, [fetchDrafts, router]);

  const handleRemove = (id: string) => {
    setDrafts((prev) => prev.filter((d) => d.message_id !== id));
    fetchDrafts(); // re-fetch to ensure sync
  };

  return (
    <div className="flex h-screen bg-gray-50">
      {/* Sidebar */}
      <aside className="w-56 bg-white border-r border-gray-200 flex flex-col p-4 gap-3 shrink-0">
        <p className="font-semibold text-sm">SalesRep</p>
        
        <nav className="space-y-1 mt-4">
          <Link
            href="/chat"
            className="block w-full text-left text-sm text-gray-600 hover:bg-gray-50 rounded-lg px-3 py-2 transition-colors"
          >
            Chat
          </Link>
          <Link
            href="/inbox"
            className="block w-full text-left text-sm bg-blue-50 text-blue-700 font-medium rounded-lg px-3 py-2 transition-colors"
          >
            Inbox
            {drafts.length > 0 && (
              <span className="ml-2 inline-flex items-center justify-center bg-blue-100 text-blue-700 text-xs font-bold px-2 py-0.5 rounded-full">
                {drafts.length}
              </span>
            )}
          </Link>
        </nav>

        <div className="flex-1" />

        <button
          onClick={() => {
            localStorage.clear();
            router.push("/login");
          }}
          className="w-full text-left text-sm text-gray-400 hover:text-gray-700 px-1 transition-colors"
        >
          Log out
        </button>
      </aside>

      {/* Main content */}
      <main className="flex-1 overflow-y-auto px-8 py-8">
        <div className="max-w-2xl mx-auto space-y-6">
          <div className="flex items-center justify-between pb-4 border-b border-gray-200">
            <h1 className="text-xl font-semibold text-gray-900">Pending Approvals</h1>
          </div>

          {loading && drafts.length === 0 ? (
            <div className="text-sm text-gray-500 text-center py-12">Loading drafts...</div>
          ) : drafts.length === 0 ? (
            <div className="text-sm text-gray-500 text-center py-12 bg-white rounded-2xl border border-gray-200 border-dashed">
              No pending drafts
            </div>
          ) : (
            <div className="space-y-6">
              {drafts.map((draft) => (
                <DraftItem key={draft.message_id} draft={draft} onRemove={handleRemove} />
              ))}
            </div>
          )}
        </div>
      </main>
    </div>
  );
}
