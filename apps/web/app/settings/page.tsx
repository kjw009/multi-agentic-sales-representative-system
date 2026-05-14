"use client";

import { useEffect, useState, useCallback } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import {
  api,
  AutonomyLevel,
  DraftStats,
  RepriceEvent,
  SellerSettings,
} from "@/lib/api";

const AUTONOMY_OPTIONS: {
  value: AutonomyLevel;
  label: string;
  description: string;
}[] = [
  {
    value: "draft",
    label: "Draft mode",
    description: "Every reply is queued for your approval. Safest default.",
  },
  {
    value: "auto_low_risk",
    label: "Auto low-risk",
    description:
      "Auto-send factual answers and polite declines. Counter-offers, accepts, and escalations still need approval.",
  },
  {
    value: "full_auto",
    label: "Full auto",
    description:
      "Auto-send all replies except accepting an offer or escalating to you. Use with care.",
  },
];

function formatGBP(amount: number) {
  return `£${amount.toFixed(2)}`;
}

function formatDateTime(iso: string) {
  return new Date(iso).toLocaleString();
}

export default function SettingsPage() {
  const router = useRouter();
  const [settings, setSettings] = useState<SellerSettings | null>(null);
  const [stats, setStats] = useState<DraftStats | null>(null);
  const [reprices, setReprices] = useState<RepriceEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [saveMsg, setSaveMsg] = useState<string | null>(null);

  const fetchAll = useCallback(async () => {
    try {
      const [s, stat, hist] = await Promise.all([
        api.getSettings(),
        api.getDraftStats(),
        api.getRepriceHistory(),
      ]);
      setSettings(s);
      setStats(stat);
      setReprices(hist);
    } catch (err) {
      console.error("Failed to load settings", err);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!localStorage.getItem("token")) {
      router.push("/login");
      return;
    }
    fetchAll();
  }, [fetchAll, router]);

  const update = (patch: Partial<SellerSettings>) => {
    if (!settings) return;
    setSettings({ ...settings, ...patch });
  };

  const handleSave = async () => {
    if (!settings) return;
    setSaving(true);
    setSaveMsg(null);
    try {
      const updated = await api.updateSettings(settings);
      setSettings(updated);
      setSaveMsg("Saved");
    } catch (err) {
      setSaveMsg(err instanceof Error ? err.message : "Failed to save");
    } finally {
      setSaving(false);
      setTimeout(() => setSaveMsg(null), 3000);
    }
  };

  return (
    <div className="flex h-screen bg-gray-50">
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
            className="block w-full text-left text-sm text-gray-600 hover:bg-gray-50 rounded-lg px-3 py-2 transition-colors"
          >
            Inbox
          </Link>
          <Link
            href="/settings"
            className="block w-full text-left text-sm bg-blue-50 text-blue-700 font-medium rounded-lg px-3 py-2 transition-colors"
          >
            Settings
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

      <main className="flex-1 overflow-y-auto px-8 py-8">
        <div className="max-w-3xl mx-auto space-y-8">
          <header className="pb-4 border-b border-gray-200">
            <h1 className="text-xl font-semibold text-gray-900">Settings</h1>
          </header>

          {loading ? (
            <div className="text-sm text-gray-500">Loading…</div>
          ) : !settings ? (
            <div className="text-sm text-red-600">Failed to load settings.</div>
          ) : (
            <>
              <section className="bg-white rounded-2xl border border-gray-200 p-6 space-y-5">
                <div>
                  <h2 className="font-semibold text-gray-900">
                    Reply autonomy
                  </h2>
                  <p className="text-sm text-gray-500 mt-1">
                    How much of the buyer-message inbox the agent handles
                    without your sign-off.
                  </p>
                </div>

                <div className="space-y-2">
                  {AUTONOMY_OPTIONS.map((opt) => (
                    <label
                      key={opt.value}
                      className={`block border rounded-xl px-4 py-3 cursor-pointer transition-colors ${
                        settings.autonomy_level === opt.value
                          ? "border-blue-500 bg-blue-50"
                          : "border-gray-200 hover:bg-gray-50"
                      }`}
                    >
                      <div className="flex items-center gap-3">
                        <input
                          type="radio"
                          name="autonomy"
                          value={opt.value}
                          checked={settings.autonomy_level === opt.value}
                          onChange={() =>
                            update({ autonomy_level: opt.value })
                          }
                          className="accent-blue-600"
                        />
                        <span className="font-medium text-sm text-gray-900">
                          {opt.label}
                        </span>
                      </div>
                      <p className="text-xs text-gray-600 mt-1 ml-7">
                        {opt.description}
                      </p>
                    </label>
                  ))}
                </div>
              </section>

              <section className="bg-white rounded-2xl border border-gray-200 p-6 space-y-5">
                <div>
                  <h2 className="font-semibold text-gray-900">
                    Stale-listing reprice
                  </h2>
                  <p className="text-sm text-gray-500 mt-1">
                    The agent will drop the price on listings nobody has
                    messaged in a while, up to a hard cap.
                  </p>
                </div>

                <div className="grid grid-cols-2 gap-5">
                  <div>
                    <label className="block text-xs font-medium uppercase tracking-wide text-gray-500 mb-1">
                      Stale after (days)
                    </label>
                    <input
                      type="number"
                      min={1}
                      max={90}
                      value={settings.stale_threshold_days}
                      onChange={(e) =>
                        update({
                          stale_threshold_days: Number(e.target.value),
                        })
                      }
                      className="w-full border border-gray-200 rounded-xl px-4 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                    />
                  </div>
                  <div>
                    <label className="block text-xs font-medium uppercase tracking-wide text-gray-500 mb-1">
                      Max reprices per listing
                    </label>
                    <input
                      type="number"
                      min={0}
                      max={10}
                      value={settings.max_reprice_count}
                      onChange={(e) =>
                        update({
                          max_reprice_count: Number(e.target.value),
                        })
                      }
                      className="w-full border border-gray-200 rounded-xl px-4 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                    />
                  </div>
                </div>
              </section>

              <div className="flex items-center justify-end gap-4">
                {saveMsg && (
                  <span
                    className={`text-sm ${
                      saveMsg === "Saved"
                        ? "text-emerald-600"
                        : "text-red-600"
                    }`}
                  >
                    {saveMsg}
                  </span>
                )}
                <button
                  onClick={handleSave}
                  disabled={saving}
                  className="px-5 py-2 text-sm font-medium bg-gray-900 text-white hover:bg-gray-800 rounded-xl disabled:opacity-50 transition-colors"
                >
                  {saving ? "Saving…" : "Save settings"}
                </button>
              </div>

              {stats && (
                <section className="bg-white rounded-2xl border border-gray-200 p-6">
                  <h2 className="font-semibold text-gray-900">Draft activity</h2>
                  <p className="text-sm text-gray-500 mt-1 mb-4">
                    How often you keep the agent's suggested reply versus
                    rewriting it.
                  </p>
                  <div className="grid grid-cols-4 gap-3 text-center">
                    <Stat label="Pending" value={stats.pending} />
                    <Stat label="Approved" value={stats.approved} />
                    <Stat label="Edited" value={stats.edited} />
                    <Stat
                      label="Edit rate"
                      value={`${Math.round(stats.edit_rate * 100)}%`}
                    />
                  </div>
                </section>
              )}

              <section className="bg-white rounded-2xl border border-gray-200 p-6">
                <h2 className="font-semibold text-gray-900">
                  Reprice history
                </h2>
                <p className="text-sm text-gray-500 mt-1 mb-4">
                  Most recent automatic reprices, newest first.
                </p>
                {reprices.length === 0 ? (
                  <div className="text-sm text-gray-500 text-center py-8 border border-dashed border-gray-200 rounded-xl">
                    No reprices yet.
                  </div>
                ) : (
                  <ul className="divide-y divide-gray-100">
                    {reprices.map((r) => (
                      <li
                        key={r.id}
                        className="py-3 flex items-center justify-between text-sm"
                      >
                        <div className="min-w-0 flex-1 mr-4">
                          <p className="font-medium text-gray-900 truncate">
                            {r.listing_url ? (
                              <a
                                href={r.listing_url}
                                target="_blank"
                                rel="noreferrer"
                                className="hover:underline"
                              >
                                {r.item_name}
                              </a>
                            ) : (
                              r.item_name
                            )}
                          </p>
                          <p className="text-xs text-gray-500">
                            {formatDateTime(r.repriced_at)}
                          </p>
                        </div>
                        <div className="text-right shrink-0">
                          <span className="text-gray-400 line-through mr-2">
                            {formatGBP(r.old_price)}
                          </span>
                          <span className="font-semibold text-gray-900">
                            {formatGBP(r.new_price)}
                          </span>
                        </div>
                      </li>
                    ))}
                  </ul>
                )}
              </section>
            </>
          )}
        </div>
      </main>
    </div>
  );
}

function Stat({ label, value }: { label: string; value: number | string }) {
  return (
    <div className="rounded-xl bg-gray-50 px-3 py-3">
      <p className="text-xs uppercase tracking-wide text-gray-500">{label}</p>
      <p className="text-lg font-semibold text-gray-900 mt-1">{value}</p>
    </div>
  );
}
