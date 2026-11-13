"use client";
import React from "react";
import Link from "next/link";
import { BarChart3 } from "lucide-react";

export default function ReportsPage() {
  return (
    <div className="min-h-screen bg-gray-950 text-gray-100 p-4 md:p-8">
      <div className="max-w-4xl mx-auto">
        {/* Page title */}
        <div className="flex items-center gap-3 mb-8 border-b border-gray-800 pb-4">
          <BarChart3 className="w-6 h-6 text-amber-400" />
          <h1 className="text-2xl font-bold tracking-tight">Reports & Analytics</h1>
        </div>

        {/* Content */}
        <div className="bg-gray-900 border border-gray-800 rounded-2xl p-8 text-center space-y-4">
          <BarChart3 className="w-16 h-16 text-amber-500/30 mx-auto" />
          <h2 className="text-xl font-bold text-gray-200">Reports Dashboard</h2>
          <p className="text-gray-400 text-sm max-w-md mx-auto">
            Access automated B2B sales summaries, regional consumption maps, SKU sensory response charts, and tender win-loss statements.
          </p>
          <div className="pt-4">
            <Link href="/dashboard" className="px-6 py-2.5 bg-amber-500 hover:bg-amber-600 text-gray-950 font-bold text-sm rounded-lg shadow-lg hover:shadow-amber-500/10 transition-all duration-300">
              Return to Dashboard
            </Link>
          </div>
        </div>
      </div>
    </div>
  );
}
