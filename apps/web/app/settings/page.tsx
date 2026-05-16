"use client";

import { useEffect, useState, useCallback } from "react";
import { useRouter } from "next/navigation";
import { Zap, CreditCard, ExternalLink } from "lucide-react";
import { api, AutonomyLevel, DraftStats, RepriceEvent, SellerSettings, BillingStatus } from "@/lib/api";
import { AppShell } from "@/components/AppShell";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";

const AUTONOMY_OPTIONS: { value: AutonomyLevel; label: string; description: string }[] = [
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
    description: "Auto-send all replies except accepting an offer. Use with care.",
  },
];

function formatGBP(n: number) { return `£${n.toFixed(2)}`; }
function formatDate(iso: string) { return new Date(iso).toLocaleString(); }

function Stat({ label, value }: { label: string; value: number | string }) {
  return (
    <div className="rounded-xl bg-muted px-3 py-3 text-center">
      <p className="text-xs uppercase tracking-wide text-muted-foreground">{label}</p>
      <p className="text-lg font-semibold mt-1">{value}</p>
    </div>
  );
}

/* ── Billing card ───────────────────────────────────────────────────── */

function BillingCard({ billing }: { billing: BillingStatus }) {
  const [loading, setLoading] = useState(false);

  async function upgrade() {
    setLoading(true);
    try {
      const { url } = await api.createCheckoutSession();
      window.location.href = url;
    } catch { setLoading(false); }
  }

  async function manage() {
    setLoading(true);
    try {
      const { url } = await api.createPortalSession();
      window.location.href = url;
    } catch { setLoading(false); }
  }

  const isPro = billing.plan === "pro";
  const isActive = billing.subscription_status === "active" || billing.subscription_status === "trialing";

  return (
    <Card>
      <CardHeader>
        <div className="flex flex-wrap items-center justify-between gap-2">
          <CardTitle>Subscription</CardTitle>
          <Badge variant={isPro && isActive ? "success" : "secondary"}>
            {isPro ? "Pro" : "Free"}
          </Badge>
        </div>
        <CardDescription>
          {isPro && isActive
            ? "You have access to all Pro features."
            : "Upgrade to Pro for unlimited listings and full automation."}
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {billing.plan === "pro" && billing.current_period_end && (
          <p className="text-sm text-muted-foreground">
            Renews {new Date(billing.current_period_end).toLocaleDateString()}
          </p>
        )}

        {isPro ? (
          <Button variant="outline" size="sm" onClick={manage} disabled={loading}>
            <ExternalLink size={13} />
            Manage subscription
          </Button>
        ) : (
          <div className="space-y-3">
            <ul className="text-sm text-muted-foreground space-y-1">
              <li className="flex items-center gap-2"><Zap size={12} className="text-amber-500" /> Unlimited listings</li>
              <li className="flex items-center gap-2"><Zap size={12} className="text-amber-500" /> Full auto-reply mode</li>
              <li className="flex items-center gap-2"><Zap size={12} className="text-amber-500" /> Automatic stale reprice</li>
            </ul>
            <Button onClick={upgrade} disabled={loading} className="w-full sm:w-auto">
              <CreditCard size={14} />
              {loading ? "Redirecting…" : "Upgrade to Pro"}
            </Button>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

/* ── Main page ──────────────────────────────────────────────────────── */

export default function SettingsPage() {
  const router = useRouter();
  const [settings, setSettings] = useState<SellerSettings | null>(null);
  const [stats, setStats] = useState<DraftStats | null>(null);
  const [reprices, setReprices] = useState<RepriceEvent[]>([]);
  const [billing, setBilling] = useState<BillingStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [saveMsg, setSaveMsg] = useState<string | null>(null);

  const fetchAll = useCallback(async () => {
    try {
      const [s, stat, hist, bill] = await Promise.all([
        api.getSettings(),
        api.getDraftStats(),
        api.getRepriceHistory(),
        api.getBillingStatus().catch(() => null),
      ]);
      setSettings(s);
      setStats(stat);
      setReprices(hist);
      setBilling(bill);
    } catch (err) {
      console.error("Failed to load settings", err);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!localStorage.getItem("token")) { router.push("/login"); return; }
    fetchAll();
  }, [fetchAll, router]);

  const update = (patch: Partial<SellerSettings>) => {
    if (!settings) return;
    setSettings({ ...settings, ...patch });
  };

  const handleSave = async () => {
    if (!settings) return;
    setSaving(true); setSaveMsg(null);
    try {
      const updated = await api.updateSettings(settings);
      setSettings(updated);
      setSaveMsg("Saved");
    } catch (err) {
      setSaveMsg(err instanceof Error ? err.message : "Failed");
    } finally {
      setSaving(false);
      setTimeout(() => setSaveMsg(null), 3000);
    }
  };

  return (
    <AppShell>
      <div className="px-4 py-6 sm:px-8 sm:py-8">
        <div className="max-w-3xl mx-auto space-y-8">
          <header className="pb-4 border-b border-border">
            <h1 className="text-xl font-semibold">Settings</h1>
          </header>

          {loading ? (
            <div className="space-y-6">
              {[1, 2, 3].map((n) => (
                <Card key={n}><CardContent className="p-6 space-y-3">
                  <Skeleton className="h-5 w-40" />
                  <Skeleton className="h-16 w-full" />
                </CardContent></Card>
              ))}
            </div>
          ) : !settings ? (
            <p className="text-sm text-destructive">Failed to load settings.</p>
          ) : (
            <>
              {/* Billing */}
              {billing && <BillingCard billing={billing} />}

              {/* Autonomy */}
              <Card>
                <CardHeader>
                  <CardTitle>Reply autonomy</CardTitle>
                  <CardDescription>
                    How much of the buyer inbox the agent handles without your sign-off.
                  </CardDescription>
                </CardHeader>
                <CardContent className="space-y-2">
                  {AUTONOMY_OPTIONS.map((opt) => (
                    <label
                      key={opt.value}
                      className={`block border rounded-xl px-4 py-3 cursor-pointer transition-colors ${
                        settings.autonomy_level === opt.value
                          ? "border-primary bg-accent"
                          : "border-border hover:bg-accent/40"
                      }`}
                    >
                      <div className="flex items-center gap-3">
                        <input
                          type="radio"
                          name="autonomy"
                          value={opt.value}
                          checked={settings.autonomy_level === opt.value}
                          onChange={() => update({ autonomy_level: opt.value })}
                          className="accent-primary"
                        />
                        <span className="font-medium text-sm">{opt.label}</span>
                      </div>
                      <p className="text-xs text-muted-foreground mt-1 ml-7">{opt.description}</p>
                    </label>
                  ))}
                </CardContent>
              </Card>

              {/* Reprice */}
              <Card>
                <CardHeader>
                  <CardTitle>Stale-listing reprice</CardTitle>
                  <CardDescription>
                    Automatically drop the price on listings nobody has messaged in a while.
                  </CardDescription>
                </CardHeader>
                <CardContent>
                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                    <div>
                      <label className="block text-xs font-medium uppercase tracking-wide text-muted-foreground mb-1">
                        Stale after (days)
                      </label>
                      <input
                        type="number" min={1} max={90}
                        value={settings.stale_threshold_days}
                        onChange={(e) => update({ stale_threshold_days: Number(e.target.value) })}
                        className="w-full border border-input bg-card rounded-xl px-4 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-ring"
                      />
                    </div>
                    <div>
                      <label className="block text-xs font-medium uppercase tracking-wide text-muted-foreground mb-1">
                        Max reprices per listing
                      </label>
                      <input
                        type="number" min={0} max={10}
                        value={settings.max_reprice_count}
                        onChange={(e) => update({ max_reprice_count: Number(e.target.value) })}
                        className="w-full border border-input bg-card rounded-xl px-4 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-ring"
                      />
                    </div>
                  </div>
                </CardContent>
              </Card>

              {/* Save */}
              <div className="flex flex-col-reverse gap-3 sm:flex-row sm:items-center sm:justify-end sm:gap-4">
                {saveMsg && (
                  <span className={`text-sm ${saveMsg === "Saved" ? "text-emerald-600" : "text-destructive"}`}>
                    {saveMsg}
                  </span>
                )}
                <Button onClick={handleSave} disabled={saving} className="w-full sm:w-auto">
                  {saving ? "Saving…" : "Save settings"}
                </Button>
              </div>

              {/* Draft activity */}
              {stats && (
                <Card>
                  <CardHeader>
                    <CardTitle>Draft activity</CardTitle>
                    <CardDescription>How often you keep vs rewrite the agent's suggestions.</CardDescription>
                  </CardHeader>
                  <CardContent>
                    <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
                      <Stat label="Pending"  value={stats.pending} />
                      <Stat label="Approved" value={stats.approved} />
                      <Stat label="Edited"   value={stats.edited} />
                      <Stat label="Edit rate" value={`${Math.round(stats.edit_rate * 100)}%`} />
                    </div>
                  </CardContent>
                </Card>
              )}

              {/* Reprice history */}
              <Card>
                <CardHeader>
                  <CardTitle>Reprice history</CardTitle>
                  <CardDescription>Most recent automatic reprices, newest first.</CardDescription>
                </CardHeader>
                <CardContent>
                  {reprices.length === 0 ? (
                    <div className="text-sm text-muted-foreground text-center py-8 border border-dashed border-border rounded-xl">
                      No reprices yet.
                    </div>
                  ) : (
                    <ul className="divide-y divide-border">
                      {reprices.map((r) => (
                        <li key={r.id} className="py-3 flex flex-col gap-2 text-sm sm:flex-row sm:items-center sm:justify-between">
                          <div className="min-w-0 flex-1">
                            <p className="font-medium truncate">
                              {r.listing_url ? (
                                <a href={r.listing_url} target="_blank" rel="noreferrer" className="hover:underline">
                                  {r.item_name}
                                </a>
                              ) : r.item_name}
                            </p>
                            <p className="text-xs text-muted-foreground">{formatDate(r.repriced_at)}</p>
                          </div>
                          <div className="shrink-0 sm:text-right">
                            <span className="text-muted-foreground line-through mr-2">{formatGBP(r.old_price)}</span>
                            <span className="font-semibold">{formatGBP(r.new_price)}</span>
                          </div>
                        </li>
                      ))}
                    </ul>
                  )}
                </CardContent>
              </Card>
            </>
          )}
        </div>
      </div>
    </AppShell>
  );
}
