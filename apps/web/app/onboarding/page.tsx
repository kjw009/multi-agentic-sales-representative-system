"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { CheckCircle2, ChevronRight, Link as LinkIcon, Bell, MessageSquare } from "lucide-react";
import { api, AutonomyLevel } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { cn } from "@/lib/utils";

const STEPS = ["Connect eBay", "Reply settings", "You're ready"] as const;

const AUTONOMY_OPTIONS: { value: AutonomyLevel; label: string; description: string }[] = [
  {
    value: "draft",
    label: "Draft mode",
    description: "Every reply queued for your approval. Recommended to start.",
  },
  {
    value: "auto_low_risk",
    label: "Auto low-risk",
    description: "Auto-send factual answers and polite declines only.",
  },
  {
    value: "full_auto",
    label: "Full auto",
    description: "Auto-send all replies except accepting an offer.",
  },
];

export default function OnboardingPage() {
  const router = useRouter();
  const [step, setStep] = useState(0);
  const [ebayConnected, setEbayConnected] = useState(false);
  const [connectingEbay, setConnectingEbay] = useState(false);
  const [autonomy, setAutonomy] = useState<AutonomyLevel>("draft");
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (!localStorage.getItem("token")) { router.push("/login"); return; }
    api.ebayStatus().then((s) => setEbayConnected(s.connected)).catch(() => {});
  }, [router]);

  async function connectEbay() {
    setConnectingEbay(true);
    try {
      const { authorization_url } = await api.ebayConnect();
      window.location.href = authorization_url;
    } catch { setConnectingEbay(false); }
  }

  async function finishOnboarding() {
    setSaving(true);
    try {
      await api.updateSettings({ autonomy_level: autonomy });
      await api.completeOnboarding();
      router.push("/chat");
    } catch {
      setSaving(false);
    }
  }

  return (
    <div className="min-h-screen bg-background flex flex-col items-center justify-center px-4 py-12">
      <div className="w-full max-w-lg space-y-8">
        {/* Brand */}
        <div className="text-center">
          <p className="font-semibold text-lg">SalesRep</p>
          <p className="text-sm text-muted-foreground mt-1">Set up your account in 2 quick steps</p>
        </div>

        {/* Step indicator */}
        <div className="flex items-center justify-center gap-2">
          {STEPS.map((label, i) => (
            <div key={label} className="flex items-center gap-2">
              <div
                className={cn(
                  "w-7 h-7 rounded-full flex items-center justify-center text-xs font-semibold transition-colors",
                  i < step
                    ? "bg-emerald-600 text-white"
                    : i === step
                    ? "bg-primary text-primary-foreground"
                    : "bg-muted text-muted-foreground"
                )}
              >
                {i < step ? <CheckCircle2 size={14} /> : i + 1}
              </div>
              <span className={cn("text-xs hidden sm:inline", i === step ? "font-medium" : "text-muted-foreground")}>
                {label}
              </span>
              {i < STEPS.length - 1 && <ChevronRight size={14} className="text-muted-foreground mx-1" />}
            </div>
          ))}
        </div>

        {/* Step 0 — eBay connect */}
        {step === 0 && (
          <Card>
            <CardHeader>
              <div className="flex items-center gap-2">
                <LinkIcon size={18} className="text-primary" />
                <CardTitle>Connect your eBay account</CardTitle>
              </div>
              <CardDescription>
                SalesRep needs permission to read your inbox and publish listings on your behalf.
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              {ebayConnected ? (
                <div className="flex items-center gap-2 text-sm text-emerald-700 bg-emerald-50 border border-emerald-200 rounded-xl px-4 py-3">
                  <CheckCircle2 size={15} className="shrink-0" />
                  eBay is connected
                </div>
              ) : (
                <Button onClick={connectEbay} disabled={connectingEbay} className="w-full sm:w-auto">
                  {connectingEbay ? "Redirecting to eBay…" : "Connect eBay"}
                </Button>
              )}
              <div className="flex justify-end pt-2">
                <Button
                  variant={ebayConnected ? "default" : "ghost"}
                  size="sm"
                  onClick={() => setStep(1)}
                >
                  {ebayConnected ? "Continue" : "Skip for now"}
                  <ChevronRight size={14} />
                </Button>
              </div>
            </CardContent>
          </Card>
        )}

        {/* Step 1 — autonomy */}
        {step === 1 && (
          <Card>
            <CardHeader>
              <div className="flex items-center gap-2">
                <Bell size={18} className="text-primary" />
                <CardTitle>How should the agent reply?</CardTitle>
              </div>
              <CardDescription>
                Choose how much of your buyer inbox the agent handles automatically. You can change this any time in Settings.
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-3">
              {AUTONOMY_OPTIONS.map((opt) => (
                <label
                  key={opt.value}
                  className={cn(
                    "block border rounded-xl px-4 py-3 cursor-pointer transition-colors",
                    autonomy === opt.value
                      ? "border-primary bg-accent"
                      : "border-border hover:bg-accent/40"
                  )}
                >
                  <div className="flex items-center gap-3">
                    <input
                      type="radio"
                      name="autonomy"
                      value={opt.value}
                      checked={autonomy === opt.value}
                      onChange={() => setAutonomy(opt.value)}
                      className="accent-primary"
                    />
                    <span className="font-medium text-sm">{opt.label}</span>
                  </div>
                  <p className="text-xs text-muted-foreground mt-1 ml-7">{opt.description}</p>
                </label>
              ))}
              <div className="flex justify-between pt-2">
                <Button variant="ghost" size="sm" onClick={() => setStep(0)}>Back</Button>
                <Button size="sm" onClick={() => setStep(2)}>
                  Continue <ChevronRight size={14} />
                </Button>
              </div>
            </CardContent>
          </Card>
        )}

        {/* Step 2 — done */}
        {step === 2 && (
          <Card>
            <CardHeader>
              <div className="flex items-center gap-2">
                <MessageSquare size={18} className="text-primary" />
                <CardTitle>You&apos;re all set!</CardTitle>
              </div>
              <CardDescription>
                Head to the chat to list your first item. The agent will handle buyer messages based on the mode you chose.
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              <ul className="text-sm text-muted-foreground space-y-1.5">
                <li className="flex items-center gap-2">
                  <CheckCircle2 size={13} className="text-emerald-600 shrink-0" />
                  {ebayConnected ? "eBay account connected" : "eBay connect skipped (connect later in Settings)"}
                </li>
                <li className="flex items-center gap-2">
                  <CheckCircle2 size={13} className="text-emerald-600 shrink-0" />
                  Reply mode: <span className="font-medium text-foreground capitalize">{autonomy.replace(/_/g, " ")}</span>
                </li>
              </ul>
              <div className="flex justify-between pt-2">
                <Button variant="ghost" size="sm" onClick={() => setStep(1)}>Back</Button>
                <Button onClick={finishOnboarding} disabled={saving}>
                  {saving ? "Saving…" : "Go to chat"}
                </Button>
              </div>
            </CardContent>
          </Card>
        )}
      </div>
    </div>
  );
}
