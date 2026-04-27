const BASE = "/api";

function token(): string | null {
  if (typeof window === "undefined") return null;
  return localStorage.getItem("token");
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers: Record<string, string> = {};

  // Don't set Content-Type for FormData — browser sets it with the correct boundary
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
}

export interface ImageUploadResponse {
  id: string;
  url: string;
  position: number;
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

  ebayConnect: () =>
    request<{ authorization_url: string }>("/auth/ebay/connect"),

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
};
