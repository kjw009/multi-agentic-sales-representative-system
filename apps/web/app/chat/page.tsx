"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { api } from "@/lib/api";

interface Message {
  role: "user" | "assistant";
  content: string;
}

export default function ChatPage() {
  const router = useRouter();
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [ebayConnected, setEbayConnected] = useState(false);
  const [connectingEbay, setConnectingEbay] = useState(false);
  const bottomRef = useRef<HTMLDivElement>(null);

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
      const reply = await api.sendMessage(content);
      setMessages((prev) => [...prev, { role: "assistant", content: reply.content }]);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Error";
      setMessages((prev) => [...prev, { role: "assistant", content: `⚠ ${msg}` }]);
    } finally {
      setLoading(false);
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

        {!ebayConnected ? (
          <button
            onClick={handleConnectEbay}
            disabled={connectingEbay}
            className="w-full text-left text-sm bg-blue-600 text-white rounded-lg px-3 py-2 hover:bg-blue-700 disabled:opacity-50 transition-colors"
          >
            {connectingEbay ? "Redirecting…" : "Connect eBay"}
          </button>
        ) : (
          <p className="text-xs text-green-600 font-medium px-1">✓ eBay connected</p>
        )}

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
            <div
              key={i}
              className={`flex ${m.role === "user" ? "justify-end" : "justify-start"}`}
            >
              <div
                className={`max-w-lg rounded-2xl px-4 py-2.5 text-sm leading-relaxed ${
                  m.role === "user"
                    ? "bg-gray-900 text-white rounded-br-sm"
                    : "bg-white border border-gray-200 text-gray-800 rounded-bl-sm"
                }`}
              >
                {m.content}
              </div>
            </div>
          ))}
          {loading && (
            <div className="flex justify-start">
              <div className="bg-white border border-gray-200 rounded-2xl rounded-bl-sm px-4 py-2.5 text-sm text-gray-400">
                …
              </div>
            </div>
          )}
          <div ref={bottomRef} />
        </div>

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
            disabled={loading || !input.trim()}
            className="bg-gray-900 text-white rounded-xl px-4 py-2 text-sm font-medium hover:bg-gray-700 disabled:opacity-40 transition-colors"
          >
            Send
          </button>
        </form>
      </main>
    </div>
  );
}
