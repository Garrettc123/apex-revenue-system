import { z } from 'zod';

export const EvidenceReferenceSchema = z.object({
  evidence_id: z.string(),
  source_system: z.string(),
  source_locator: z.string(),
  content_hash: z.string().optional(),
  observed_at: z.string().optional(),
  retrieved_at: z.string(),
  authority_level: z.enum(['primary', 'internal_system', 'verified_external', 'secondary', 'unverified']),
  freshness_score: z.number().min(0).max(1),
  integrity_status: z.enum(['verified', 'unknown', 'failed'])
});

export const PainPointCaseSchema = z.object({
  pain_case_id: z.string(),
  tenant_id: z.string(),
  trace_id: z.string(),
  account_id: z.string().optional(),
  contact_id: z.string().optional(),
  domain: z.enum(['revenue', 'retention', 'sales_operations', 'content_operations', 'software_delivery', 'data_integrity', 'customer_success']),
  symptom: z.string(),
  root_cause_hypotheses: z.array(z.object({
    hypothesis: z.string(),
    confidence: z.number().min(0).max(1),
    evidence_refs: z.array(z.string())
  })),
  evidence: z.array(EvidenceReferenceSchema),
  confidence: z.number().min(0).max(1),
  freshness_score: z.number().min(0).max(1),
  pain_severity: z.enum(['low', 'medium', 'high', 'critical']),
  economic_impact_estimate: z.object({
    type: z.enum(['measured', 'derived', 'illustrative', 'unknown']),
    formula: z.string().optional(),
    estimated_value: z.number().optional(),
    currency: z.string().optional(),
    period: z.string().optional(),
    assumptions: z.array(z.string()).optional()
  }).optional(),
  matched_solution: z.object({
    offer_id: z.string(),
    offer_name: z.string(),
    match_score: z.number().min(0).max(1)
  }).optional(),
  readiness_state: z.enum(['detected', 'needs_evidence', 'validated', 'solution_matched', 'offer_ready', 'approval_required', 'executing', 'won', 'lost', 'deferred']),
  next_best_action: z.object({
    action_type: z.string(),
    description: z.string(),
    requires_approval: z.boolean()
  }),
  disqualifiers: z.array(z.string()),
  created_at: z.string(),
  updated_at: z.string()
});

export const OfferDefinitionSchema = z.object({
  offer_id: z.string(),
  name: z.string(),
  target_pains: z.array(z.string()),
  target_buyer_roles: z.array(z.string()),
  required_signals: z.array(z.string()),
  disqualifying_signals: z.array(z.string()),
  deliverables: z.array(z.string()),
  implementation_window: z.string(),
  pricing_model: z.enum(['fixed', 'subscription', 'usage', 'hybrid']),
  price_floor: z.number().optional(),
  price_ceiling: z.number().optional(),
  proof_assets: z.array(z.string()),
  prerequisites: z.array(z.string()),
  outcome_metrics: z.array(z.string()),
  fulfillment_playbook_id: z.string(),
  checkout_product_id: z.string().optional(),
  approval_requirements: z.array(z.string())
});

export const AccountIntelligenceBriefSchema = z.object({
  account_id: z.string(),
  canonical_name: z.string(),
  buyer_hypothesis: z.string(),
  verified_facts: z.array(z.object({
    fact: z.string(),
    source: z.string(),
    verified_at: z.string()
  })),
  observed_signals: z.array(z.object({
    signal: z.string(),
    source: z.string(),
    observed_at: z.string()
  })),
  pain_hypotheses: z.array(z.object({
    pain: z.string(),
    confidence: z.number().min(0).max(1),
    evidence_refs: z.array(z.string())
  })),
  likely_economic_effect: z.object({
    description: z.string(),
    estimated_value: z.number().optional(),
    currency: z.string(),
    assumptions: z.array(z.string())
  }).optional(),
  offer_match: z.object({
    offer_id: z.string(),
    offer_name: z.string(),
    rationale: z.string()
  }),
  proof_assets: z.array(z.string()),
  risks_and_unknowns: z.array(z.string()),
  suggested_next_step: z.string(),
  outreach_eligibility: z.enum(['eligible', 'needs_review', 'ineligible']),
  confidence: z.number().min(0).max(1),
  evidence_refs: z.array(z.string())
});

export type EvidenceReference = z.infer<typeof EvidenceReferenceSchema>;
export type PainPointCase = z.infer<typeof PainPointCaseSchema>;
export type OfferDefinition = z.infer<typeof OfferDefinitionSchema>;
export type AccountIntelligenceBrief = z.infer<typeof AccountIntelligenceBriefSchema>;
