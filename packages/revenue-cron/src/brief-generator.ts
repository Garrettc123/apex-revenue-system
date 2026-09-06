import type { PainPointCase, AccountIntelligenceBrief, OfferDefinition } from './types.js';
import { OFFER_REGISTRY } from './config.js';
import { randomUUID } from 'crypto';

export function generateBrief(painCase: PainPointCase & { matched_solution: NonNullable<PainPointCase['matched_solution']> }, tenantId: string): AccountIntelligenceBrief {
  const offer = OFFER_REGISTRY.find(o => o.offer_id === painCase.matched_solution.offer_id);
  const now = new Date().toISOString();
  const verified_facts = painCase.evidence.map(e => ({
    fact: `${e.source_system} record: ${e.source_locator}`,
    source: e.source_system,
    verified_at: e.retrieved_at
  }));
  const observed_signals = painCase.evidence.map(e => ({
    signal: `${e.source_system}: ${e.source_locator} observed`,
    source: e.source_system,
    observed_at: e.retrieved_at
  }));
  const pain_hypotheses = painCase.root_cause_hypotheses.map(h => ({
    pain: h.hypothesis,
    confidence: h.confidence,
    evidence_refs: h.evidence_refs
  }));
  const economic = painCase.economic_impact_estimate;
  const likely_economic_effect = economic ? {
    description: `Estimated ${economic.period} impact: ${economic.formula || 'unknown'}`, 
    estimated_value: economic.estimated_value,
    currency: economic.currency || 'USD',
    assumptions: economic.assumptions || []
  } : undefined;
  const risks_and_unknowns = [
    'Account identity not yet verified against CRM',
    'Contact consent and outreach eligibility require validation',
    'Economic impact is derived/illustrative, not measured',
    'Buyer authority and decision timeline unknown'
  ];
  const brief: AccountIntelligenceBrief = {
    account_id: painCase.account_id || `unknown_${painCase.pain_case_id.slice(0, 8)}`,
    canonical_name: 'Account pending verification',
    buyer_hypothesis: 'Decision-maker likely in revenue/operations/sales leadership',
    verified_facts,
    observed_signals,
    pain_hypotheses,
    likely_economic_effect,
    offer_match: {
      offer_id: painCase.matched_solution.offer_id,
      offer_name: painCase.matched_solution.offer_name,
      rationale: `Pain pattern ${painCase.symptom} matches ${painCase.matched_solution.offer_name} with score ${painCase.matched_solution.match_score.toFixed(2)}`
    },
    proof_assets: offer?.proof_assets || [],
    risks_and_unknowns,
    suggested_next_step: 'Run paid diagnostic to verify pain and scope implementation',
    outreach_eligibility: 'needs_review',
    confidence: painCase.confidence * painCase.matched_solution.match_score,
    evidence_refs: painCase.evidence.map(e => e.evidence_id)
  };
  return brief;
}
