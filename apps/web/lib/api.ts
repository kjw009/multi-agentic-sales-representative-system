// When NEXT_PUBLIC_API_URL is set (production / Vercel), call the backend
// directly to avoid the Next.js rewrite proxy's upstream timeout.
// When unset (local dev), fall back to the relative `/api` path which the
// Next.js rewrite forwards to API_URL (defaulting to http://localhost:8000).
const BASE = process.env.NEXT_PUBLIC_API_URL ?? "/api";

function token(): string | null {
  if (typeof window === "undefined") return null;
  return localStorage.getItem("token");
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers: Record<string, string> = {};

  if (!(init.body instanceof FormData)) {
    headers["Content-Type"] = "application/json";
  }

  Object.assign(headers, init.headers);

  const t = token();
  if (t) headers["Authorization"] = `Bearer ${t}`;

  const res = await fetch(`${BASE}${path}`, { ...init, headers });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail ?? `HTTP ${res.status}`);
  }
  return res.json() as Promise<T>;
}

export interface TokenResponse {
  access_token: string;
  token_type: string;
  seller_id: string;
}

export interface MessageResponse {
  role: string;
  content: string;
  item_id: string | null;
  needs_image: boolean;
  intake_complete: boolean;
}

export interface ImageUploadResponse {
  id: string;
  url: string;
  position: number;
}

export interface Comparable {
  title: string;
  price: number;
  currency: string;
  condition: string;
  item_id: string;
  listing_url: string;
  similarity_score?: number | null;
}

export interface PricingResult {
  item_id: string;
  recommended_price: number;
  confidence_score: number;
  min_acceptable_price: number;
  price_low: number;
  price_high: number;
  comparables: Comparable[];
}

export interface EbayStatusResponse {
  connected: boolean;
  expires_at: string | null;
}

export interface ListingStatus {
  status: "pending_approval" | "publishing" | "live" | "ended" | "error" | "needs_specifics";
  url: string | null;
  external_id: string | null;
  posted_price: number | null;
  close_reason?: string | null;
  required_specifics?: string[];
}

export interface DraftMessage {
  message_id: string;
  conversation_id: string;
  buyer_handle: string;
  raw_text: string;
  draft_reply: string | null;
  received_at: string;
  listing_id: string | null;
}

export type AutonomyLevel = "draft" | "auto_low_risk" | "full_auto";

export interface SellerSettings {
  autonomy_level: AutonomyLevel;
  stale_threshold_days: number;
  max_reprice_count: number;
  require_listing_approval: boolean;
}

export interface RepriceEvent {
  id: string;
  listing_id: string;
  item_name: string;
  listing_url: string | null;
  old_price: number;
  new_price: number;
  repriced_at: string;
}

export interface DraftStats {
  pending: number;
  approved: number;
  edited: number;
  edit_rate: number;
}

export interface BillingStatus {
  plan: "free" | "pro";
  subscription_status: "none" | "trialing" | "active" | "past_due" | "canceled";
  current_period_end: string | null;
}

export const api = {
  signup: (email: string, password: string) =>
    request<TokenResponse>("/auth/signup", {
      method: "POST",
      body: JSON.stringify({ email, password }),
    }),

  login: (email: string, password: string) =>
    request<TokenResponse>("/auth/login", {
      method: "POST",
      body: JSON.stringify({ email, password }),
    }),

  demoLogin: () =>
    request<TokenResponse>("/auth/demo"),

  getOnboardingStatus: () =>
    request<boolean>("/settings/seller/onboarding-status"),

  ebayConnect: () =>
    request<{ authorization_url: string }>("/auth/ebay/connect"),

  ebayStatus: () =>
    request<EbayStatusResponse>("/auth/ebay/status"),

  sendMessage: (content: string, itemId?: string | null) =>
    request<MessageResponse>("/agent/intake/message", {
      method: "POST",
      body: JSON.stringify({ content, item_id: itemId ?? null }),
    }),

  uploadImage: (file: File, itemId: string) => {
    const form = new FormData();
    form.append("file", file);
    return request<ImageUploadResponse>(
      `/agent/intake/upload-image?item_id=${itemId}`,
      { method: "POST", body: form },
    );
  },

  // Returns null while pricing is in progress, PricingResult once complete
  getPricing: (itemId: string) =>
    request<PricingResult | null>(`/agent/intake/pricing/${itemId}`),

  // Returns null while listing is not yet created, ListingStatus once publisher runs
  getListingStatus: (itemId: string) =>
    request<ListingStatus | null>(`/agent/intake/listing/${itemId}`),

  approveListing: (itemId: string) =>
    request<{ status: string }>(`/agent/intake/listing/${itemId}/approve`, {
      method: "POST",
    }),

  getDrafts: () =>
    request<DraftMessage[]>("/conversations/drafts"),

  approveDraft: (messageId: string) =>
    request<{ status: string }>(`/conversations/${messageId}/approve`, {
      method: "POST",
    }),

  editDraft: (messageId: string, text: string) =>
    request<{ status: string }>(`/conversations/${messageId}/edit`, {
      method: "POST",
      body: JSON.stringify({ text }),
    }),

  dismissDraft: (messageId: string) =>
    request<{ status: string }>(`/conversations/${messageId}/dismiss`, {
      method: "POST",
    }),

  getSettings: () => request<SellerSettings>("/settings/seller"),

  updateSettings: (patch: Partial<SellerSettings>) =>
    request<SellerSettings>("/settings/seller", {
      method: "PATCH",
      body: JSON.stringify(patch),
    }),

  completeOnboarding: () =>
    request<{ ok: boolean }>("/settings/seller/complete-onboarding", {
      method: "POST",
    }),

  getRepriceHistory: () =>
    request<RepriceEvent[]>("/listings/reprice-history"),

  getDraftStats: () => request<DraftStats>("/conversations/stats"),

  getBillingStatus: () => request<BillingStatus>("/billing/status"),

  createCheckoutSession: () =>
    request<{ url: string }>("/billing/checkout-session", { method: "POST" }),

  createPortalSession: () =>
    request<{ url: string }>("/billing/portal-session", { method: "POST" }),
};
