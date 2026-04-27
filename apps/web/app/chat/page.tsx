"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { api } from "@/lib/api";

interface Message {
  role: "user" | "assistant";
  content: string;
  needsImage?: boolean;
  imageUrl?: string;
}

export default function ChatPage() {
  const router = useRouter();
  const [messages, setMessages] = useState<Message[]>([]);
  const [itemId, setItemId] = useState<string | null>(null);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [connectingEbay, setConnectingEbay] = useState(false);
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
  }, [router]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  async function handleConnectEbay() {
    setConnectingEbay(true);
    try {
      const { authorization_url } = await api.ebayConnect();
      window.location.href = authorization_url;
    } catch (err: unknown) {
      alert(err instanceof Error ? err.message : "Failed to start eBay connection");
      setConnectingEbay(false);
    }
  }

  async function sendMessage(e: React.FormEvent) {
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
    // Show local preview immediately
    const localUrl = URL.createObjectURL(file);
    setMessages((prev) => [...prev, { role: "user", content: "", imageUrl: localUrl }]);

    try {
      await api.uploadImage(file, itemId);
      // Tell the agent the image was uploaded so it can continue
      const reply = await api.sendMessage("I've uploaded the image.", itemId);
      if (reply.item_id) setItemId(reply.item_id);
      setMessages((prev) => [
        ...prev,
        { role: "assistant", content: reply.content, needsImage: reply.needs_image },
      ]);
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

  return (
    <div className="flex h-screen">
      {/* Sidebar */}
      <aside className="w-56 bg-white border-r border-gray-200 flex flex-col p-4 gap-3">
        <p className="font-semibold text-sm">SalesRep</p>
        <div className="flex-1" />
        <button
          onClick={handleConnectEbay}
          disabled={connectingEbay}
          className="w-full text-sm bg-blue-600 text-white rounded-lg px-3 py-2 hover:bg-blue-700 disabled:opacity-50 transition-colors"
        >
          {connectingEbay ? "Redirecting…" : "Connect eBay"}
        </button>
        <button
          onClick={logout}
          className="w-full text-left text-sm text-gray-400 hover:text-gray-700 px-1 transition-colors"
        >
          Log out
        </button>
      </aside>

      {/* Chat */}
      <main className="flex-1 flex flex-col">
        <div className="flex-1 overflow-y-auto px-6 py-6 space-y-4">
          {messages.map((m, i) => (
            <div key={i} className={`flex ${m.role === "user" ? "justify-end" : "justify-start"}`}>
              <div className="max-w-lg space-y-2">
                {/* Image preview */}
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

                {/* Text bubble */}
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

                {/* Upload button — shown on the message that requested a photo */}
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

        {/* Hidden file input */}
        <input
          ref={fileRef}
          type="file"
          accept="image/jpeg,image/png,image/gif,image/webp"
          className="hidden"
          onChange={handleFileChange}
        />

        {/* Message input */}
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
    </div>
  );
}
