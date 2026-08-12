"use client";
import React, { useEffect, useState } from "react";
import Link from "next/link";
import {
  Save, ShieldAlert, Key, Mail,
  ShoppingBag, CheckCircle, AlertCircle, RefreshCw
} from "lucide-react";

interface SettingsState {
  CEREBRAS_API_KEY: string;
  SENDER_EMAIL: string;
  SENDER_NAME: string;
  ZOHO_APP_PASSWORD: string;
  SHOPIFY_STORE: string;
  SHOPIFY_TOKEN: string;
  SHOPIFY_WEBHOOK_SECRET: string;
}

export default function SettingsPage() {
  const [settings, setSettings] = useState<SettingsState>({
    CEREBRAS_API_KEY: "",
    SENDER_EMAIL: "",
    SENDER_NAME: "",
    ZOHO_APP_PASSWORD: "",
    SHOPIFY_STORE: "",
    SHOPIFY_TOKEN: "",
    SHOPIFY_WEBHOOK_SECRET: "",
  });
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [status, setStatus] = useState<{ type: "success" | "error" | null; message: string }>({
    type: null,
    message: "",
  });

  const fetchSettings = async () => {
    setLoading(true);
    try {
      const response = await fetch("/api/v1/settings");
      if (response.ok) {
        const data = await response.json();
        setSettings(data);
      } else {
        setStatus({ type: "error", message: "Failed to retrieve settings from server." });
      }
    } catch {
      setStatus({ type: "error", message: "Cannot connect to the backend server. Is it running?" });
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchSettings();
  }, []);

  const handleChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const { name, value } = e.target;
    setSettings((prev) => ({ ...prev, [name]: value }));
  };

  const handleSave = async (e: React.FormEvent) => {
    e.preventDefault();
    setSaving(true);
    setStatus({ type: null, message: "" });
    try {
      const response = await fetch("/api/v1/settings", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(settings),
      });
      if (response.ok) {
        setStatus({ type: "success", message: "Settings saved successfully and reloaded into backend env!" });
        // Re-fetch to get new masked values
        await fetchSettings();
      } else {
        setStatus({ type: "error", message: "Failed to save settings. Please try again." });
      }
    } catch {
      setStatus({ type: "error", message: "Cannot reach server to save configurations." });
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="min-h-screen bg-gray-950 text-gray-100 p-4 md:p-8">
      {/* Page title */}
      <div className="flex items-center justify-between mb-8 max-w-4xl mx-auto">
        <div className="flex items-center gap-3">
          <Link href="/dashboard" className="flex items-center gap-2 hover:opacity-95 transition-opacity group">
            <div className="flex items-center gap-2 bg-gray-900/80 p-1.5 rounded-lg border border-gray-800 shadow-md">
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img src="/company_logo.png" alt="Pure Pantry Provisions" className="h-7 w-7 object-contain rounded" />
              <div className="h-5 w-px bg-gray-800" />
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img src="/purity_beans_logo.png" alt="Purity Beans" className="h-7 w-7 object-contain rounded" />
            </div>
          </Link>
          <div className="h-5 w-px bg-gray-800 mx-1" />
          <h1 className="text-2xl font-bold tracking-tight">System Settings</h1>
        </div>
        <button
          onClick={fetchSettings}
          disabled={loading}
          className="flex items-center gap-2 px-3 py-2 bg-gray-800 hover:bg-gray-700 rounded-lg text-sm transition-colors disabled:opacity-50"
        >
          <RefreshCw className={`w-4 h-4 ${loading ? "animate-spin" : ""}`} /> Reload
        </button>
      </div>

      {loading ? (
        <div className="flex flex-col items-center justify-center py-20 max-w-4xl mx-auto bg-gray-900 border border-gray-800 rounded-2xl">
          <RefreshCw className="w-10 h-10 text-amber-500 animate-spin mb-4" />
          <p className="text-gray-400 text-sm">Reading settings and credentials...</p>
        </div>
      ) : (
        <form onSubmit={handleSave} className="max-w-4xl mx-auto space-y-6">
          
          {/* Status Message */}
          {status.type && (
            <div
              className={`p-4 rounded-xl border flex items-start gap-3 transition-all duration-300 ${
                status.type === "success"
                  ? "bg-green-950/30 border-green-500/30 text-green-300"
                  : "bg-red-950/30 border-red-500/30 text-red-300"
              }`}
            >
              {status.type === "success" ? (
                <CheckCircle className="w-5 h-5 text-green-400 shrink-0 mt-0.5" />
              ) : (
                <AlertCircle className="w-5 h-5 text-red-400 shrink-0 mt-0.5" />
              )}
              <div>
                <p className="text-sm font-semibold">{status.type === "success" ? "Success" : "Error"}</p>
                <p className="text-xs text-gray-400 mt-0.5">{status.message}</p>
              </div>
            </div>
          )}

          {/* Section 1: AI Engines */}
          <div className="bg-gray-900 border border-gray-800 rounded-2xl p-6">
            <div className="flex items-center gap-2 mb-4 border-b border-gray-800 pb-3">
              <Key className="w-5 h-5 text-amber-400" />
              <h2 className="text-lg font-semibold text-gray-200">AI LLM Engine Keys</h2>
            </div>
            <div className="space-y-4">
              <div>
                <label className="block text-xs font-semibold uppercase tracking-wider text-gray-400 mb-1">
                  Cerebras API Key
                </label>
                <input
                  type="password"
                  name="CEREBRAS_API_KEY"
                  value={settings.CEREBRAS_API_KEY}
                  onChange={handleChange}
                  placeholder="Enter Cerebras API Key (starts with csk-)"
                  className="w-full bg-gray-950 border border-gray-800 focus:border-amber-500 focus:ring-1 focus:ring-amber-500 rounded-lg px-3 py-2 text-sm text-gray-100 placeholder-gray-600 outline-none transition-all"
                />
                <p className="text-[11px] text-gray-500 mt-1">
                  Primary inference engine for agent operations. If empty, the system defaults to simulated/pre-computed data.
                </p>
              </div>
            </div>
          </div>

          {/* Section 2: Email Outreach Settings */}
          <div className="bg-gray-900 border border-gray-800 rounded-2xl p-6">
            <div className="flex items-center gap-2 mb-4 border-b border-gray-800 pb-3">
              <Mail className="w-5 h-5 text-amber-400" />
              <h2 className="text-lg font-semibold text-gray-200">Outreach Mail Server (Zoho)</h2>
            </div>
            
            <div className="bg-amber-500/10 border border-amber-500/20 rounded-xl p-4 mb-4 text-xs text-amber-300 flex items-start gap-2">
              <ShieldAlert className="w-4 h-4 shrink-0 mt-0.5" />
              <div>
                <p className="font-semibold mb-0.5">Important Zoho configuration</p>
                <p className="text-gray-400">
                  Do NOT input your main Zoho login password. You must generate an <strong>App Password</strong> from 
                  Zoho Accounts &gt; Security &gt; App Passwords.
                </p>
              </div>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <div>
                <label className="block text-xs font-semibold uppercase tracking-wider text-gray-400 mb-1">
                  Sender Email Address
                </label>
                <input
                  type="email"
                  name="SENDER_EMAIL"
                  value={settings.SENDER_EMAIL}
                  onChange={handleChange}
                  placeholder="connect@purepantryprovisions.com"
                  className="w-full bg-gray-950 border border-gray-800 focus:border-amber-500 focus:ring-1 focus:ring-amber-500 rounded-lg px-3 py-2 text-sm text-gray-100 placeholder-gray-600 outline-none transition-all"
                />
              </div>
              <div>
                <label className="block text-xs font-semibold uppercase tracking-wider text-gray-400 mb-1">
                  Sender Display Name
                </label>
                <input
                  type="text"
                  name="SENDER_NAME"
                  value={settings.SENDER_NAME}
                  onChange={handleChange}
                  placeholder="Hiten Jain | Pure Pantry Provisions"
                  className="w-full bg-gray-950 border border-gray-800 focus:border-amber-500 focus:ring-1 focus:ring-amber-500 rounded-lg px-3 py-2 text-sm text-gray-100 placeholder-gray-600 outline-none transition-all"
                />
              </div>
              <div className="md:col-span-2">
                <label className="block text-xs font-semibold uppercase tracking-wider text-gray-400 mb-1">
                  Zoho App Password
                </label>
                <input
                  type="password"
                  name="ZOHO_APP_PASSWORD"
                  value={settings.ZOHO_APP_PASSWORD}
                  onChange={handleChange}
                  placeholder="Enter Zoho App Password"
                  className="w-full bg-gray-950 border border-gray-800 focus:border-amber-500 focus:ring-1 focus:ring-amber-500 rounded-lg px-3 py-2 text-sm text-gray-100 placeholder-gray-600 outline-none transition-all"
                />
              </div>
            </div>
          </div>

          {/* Section 3: Shopify Store Connection */}
          <div className="bg-gray-900 border border-gray-800 rounded-2xl p-6">
            <div className="flex items-center gap-2 mb-4 border-b border-gray-800 pb-3">
              <ShoppingBag className="w-5 h-5 text-amber-400" />
              <h2 className="text-lg font-semibold text-gray-200">Shopify Integration</h2>
            </div>
            
            <div className="bg-gray-950 border border-gray-800 rounded-xl p-4 mb-4 text-xs text-gray-400">
              <p className="font-semibold mb-1 text-gray-300">Setting up Shopify:</p>
              <ul className="list-disc pl-4 space-y-1">
                <li>Under Shopify Admin &gt; Apps &gt; Develop Apps, create a private custom app.</li>
                <li>Ensure API Admin scopes are enabled: <code>read_orders</code>, <code>read_products</code>, <code>read_inventory</code>.</li>
                <li>Copy the Admin Access Token (usually starts with <code>shpat_</code>).</li>
              </ul>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <div className="md:col-span-2">
                <label className="block text-xs font-semibold uppercase tracking-wider text-gray-400 mb-1">
                  Shopify Store Domain
                </label>
                <input
                  type="text"
                  name="SHOPIFY_STORE"
                  value={settings.SHOPIFY_STORE}
                  onChange={handleChange}
                  placeholder="purepantryprovisions.myshopify.com"
                  className="w-full bg-gray-950 border border-gray-800 focus:border-amber-500 focus:ring-1 focus:ring-amber-500 rounded-lg px-3 py-2 text-sm text-gray-100 placeholder-gray-600 outline-none transition-all"
                />
              </div>
              <div>
                <label className="block text-xs font-semibold uppercase tracking-wider text-gray-400 mb-1">
                  Shopify Admin Access Token
                </label>
                <input
                  type="password"
                  name="SHOPIFY_TOKEN"
                  value={settings.SHOPIFY_TOKEN}
                  onChange={handleChange}
                  placeholder="shpat_..."
                  className="w-full bg-gray-950 border border-gray-800 focus:border-amber-500 focus:ring-1 focus:ring-amber-500 rounded-lg px-3 py-2 text-sm text-gray-100 placeholder-gray-600 outline-none transition-all"
                />
              </div>
              <div>
                <label className="block text-xs font-semibold uppercase tracking-wider text-gray-400 mb-1">
                  Shopify Webhook Secret
                </label>
                <input
                  type="password"
                  name="SHOPIFY_WEBHOOK_SECRET"
                  value={settings.SHOPIFY_WEBHOOK_SECRET}
                  onChange={handleChange}
                  placeholder="Enter webhook signature secret"
                  className="w-full bg-gray-950 border border-gray-800 focus:border-amber-500 focus:ring-1 focus:ring-amber-500 rounded-lg px-3 py-2 text-sm text-gray-100 placeholder-gray-600 outline-none transition-all"
                />
              </div>
            </div>
          </div>

          {/* Form Actions */}
          <div className="flex items-center justify-end gap-3 max-w-4xl mx-auto pt-4">
            <Link href="/dashboard" className="px-4 py-2 border border-gray-800 hover:bg-gray-900 rounded-lg text-sm transition-colors">
              Cancel
            </Link>
            <button
              type="submit"
              disabled={saving}
              className="flex items-center gap-2 px-5 py-2 bg-amber-500 hover:bg-amber-600 active:scale-95 disabled:scale-100 text-gray-950 font-semibold rounded-lg text-sm transition-all shadow-md shadow-amber-500/10 disabled:opacity-50"
            >
              {saving ? (
                <RefreshCw className="w-4 h-4 animate-spin" />
              ) : (
                <Save className="w-4 h-4" />
              )}
              Save Configuration
            </button>
          </div>

        </form>
      )}
    </div>
  );
}
