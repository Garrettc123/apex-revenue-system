import { createClient, SupabaseClient } from '@supabase/supabase-js';
import { loadEnv } from '../config.js';
import type { PainPointCase, AccountIntelligenceBrief, EvidenceReference } from '../types.js';

let cachedClient: SupabaseClient | null = null;

export function getSupabaseClient(): SupabaseClient {
  if (cachedClient) return cachedClient;
  const { SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY } = loadEnv();
  cachedClient = createClient(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY, {
    auth: { persistSession: false }
  });
  return cachedClient;
}

export async function upsertPainCase(caseData: PainPointCase): Promise<void> {
  const supabase = getSupabaseClient();
  const { error } = await supabase
    .from('pain_point_cases')
    .upsert({
      pain_case_id: caseData.pain_case_id,
      tenant_id: caseData.tenant_id,
      trace_id: caseData.trace_id,
      account_id: caseData.account_id,
      contact_id: caseData.contact_id,
      domain: caseData.domain,
      symptom: caseData.symptom,
      root_cause_hypotheses: caseData.root_cause_hypotheses,
      evidence: caseData.evidence,
      confidence: caseData.confidence,
      freshness_score: caseData.freshness_score,
      pain_severity: caseData.pain_severity,
      economic_impact_estimate: caseData.economic_impact_estimate,
      matched_solution: caseData.matched_solution,
      readiness_state: caseData.readiness_state,
      next_best_action: caseData.next_best_action,
      disqualifiers: caseData.disqualifiers,
      created_at: caseData.created_at,
      updated_at: caseData.updated_at
    }, { onConflict: 'pain_case_id' });
  if (error) throw new Error(`Failed to upsert pain case: ${error.message}`);
}

export async function upsertBrief(brief: AccountIntelligenceBrief): Promise<void> {
  const supabase = getSupabaseClient();
  const { error } = await supabase
    .from('account_intelligence_briefs')
    .upsert({
      account_id: brief.account_id,
      canonical_name: brief.canonical_name,
      buyer_hypothesis: brief.buyer_hypothesis,
      verified_facts: brief.verified_facts,
      observed_signals: brief.observed_signals,
      pain_hypotheses: brief.pain_hypotheses,
      likely_economic_effect: brief.likely_economic_effect,
      offer_match: brief.offer_match,
      proof_assets: brief.proof_assets,
      risks_and_unknowns: brief.risks_and_unknowns,
      suggested_next_step: brief.suggested_next_step,
      outreach_eligibility: brief.outreach_eligibility,
      confidence: brief.confidence,
      evidence_refs: brief.evidence_refs,
      updated_at: new Date().toISOString()
    }, { onConflict: 'account_id' });
  if (error) throw new Error(`Failed to upsert brief: ${error.message}`);
}

export async function insertEvent(event: {
  tenant_id: string;
  run_id: string;
  trace_id: string;
  event_type: string;
  actor_type: string;
  actor_id: string;
  payload: unknown;
  payload_hash: string;
  previous_event_hash?: string;
  event_hash: string;
  policy_version?: string;
  system_version?: string;
}): Promise<void> {
  const supabase = getSupabaseClient();
  const { error } = await supabase
    .from('rhns_events')
    .insert(event);
  if (error) throw new Error(`Failed to insert event: ${error.message}`);
}

export async function insertEpisode(episode: {
  tenant_id: string;
  run_id: string;
  objective_summary: string;
  plan_snapshot: unknown;
  outcome: unknown;
  lessons: unknown[];
  quality_score?: number;
}): Promise<void> {
  const supabase = getSupabaseClient();
  const { error } = await supabase
    .from('episodes')
    .insert(episode);
  if (error) throw new Error(`Failed to insert episode: ${error.message}`);
}

export async function getRecentPainCases(tenantId: string, limit = 50): Promise<PainPointCase[]> {
  const supabase = getSupabaseClient();
  const { data, error } = await supabase
    .from('pain_point_cases')
    .select('*')
    .eq('tenant_id', tenantId)
    .order('created_at', { ascending: false })
    .limit(limit);
  if (error) throw new Error(`Failed to fetch pain cases: ${error.message}`);
  return (data || []) as PainPointCase[];
}

export async function getRecentBriefs(tenantId: string, limit = 50): Promise<AccountIntelligenceBrief[]> {
  const supabase = getSupabaseClient();
  const { data, error } = await supabase
    .from('account_intelligence_briefs')
    .select('*')
    .eq('tenant_id', tenantId)
    .order('updated_at', { ascending: false })
    .limit(limit);
  if (error) throw new Error(`Failed to fetch briefs: ${error.message}`);
  return (data || []) as AccountIntelligenceBrief[];
}
