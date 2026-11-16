/**
 * Per-browser conveniences only (recent analyses). The API has no endpoint that lists
 * analyses, so the UI remembers the ones run from this browser. Storage can be blocked
 * (private mode, policies), so every access is guarded and the UI works without it.
 */
import type { AnalysisRead } from "../api/client";

const KEY = "smartlog.recentAnalyses";
const MAX = 15;

export interface RecentAnalysis {
  id: string;
  createdAt: string;
  status: AnalysisRead["status"];
  logCount: number;
  summary: string | null;
}

export function loadRecent(): RecentAnalysis[] {
  try {
    const parsed: unknown = JSON.parse(localStorage.getItem(KEY) ?? "[]");
    return Array.isArray(parsed) ? (parsed as RecentAnalysis[]) : [];
  } catch {
    return [];
  }
}

export function rememberAnalysis(analysis: AnalysisRead): RecentAnalysis[] {
  const entry: RecentAnalysis = {
    id: analysis.id,
    createdAt: analysis.created_at,
    status: analysis.status,
    logCount: analysis.log_count,
    summary: analysis.summary,
  };
  const list = [entry, ...loadRecent().filter((r) => r.id !== analysis.id)].slice(0, MAX);
  try {
    localStorage.setItem(KEY, JSON.stringify(list));
  } catch {
    // Storage unavailable: the list just lives for this session.
  }
  return list;
}
