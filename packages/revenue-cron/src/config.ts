import { z } from 'zod';

const EnvSchema = z.object({
  SUPABASE_URL: z.string().url(),
  SUPABASE_SERVICE_ROLE_KEY: z.string().min(1),
  STRIPE_SECRET_KEY: z.string().min(1).optional(),
  HUBSPOT_ACCESS_TOKEN: z.string().min(1).optional(),
  SHOPIFY_ACCESS_TOKEN: z.string().min(1).optional(),
  GITHUB_TOKEN: z.string().min(1),
  NOTION_API_KEY: z.string().min(1).optional(),
  LINEAR_API_KEY: z.string().min(1).optional(),
  SLACK_BOT_TOKEN: z.string().min(1).optional(),
  TENANT_ID: z.string().min(1),
  TOP_N_BRIEFS: z.string().transform(Number).default('10')
});

export type Env = z.infer<typeof EnvSchema>;

export function loadEnv(): Env {
  const result = EnvSchema.safeParse(process.env);
  if (!result.success) {
    const missing = result.error.errors
      .map(e => e.path.join('.'))
      .join(', ');
    throw new Error(`Missing or invalid environment variables: ${missing}`);
  }
  return result.data;
}

export const OFFER_REGISTRY: OfferDefinition[] = [
  {
    offer_id: 'revenue_leak_diagnostic',
    name: 'Revenue Leak Diagnostic',
    target_pains: ['stalled_deals', 'poor_followup', 'crm_hygiene', 'pipeline_leakage'],
    target_buyer_roles: ['founder', 'head_of_sales', 'revenue_operator'],
    required_signals: ['qualified_opportunities', 'followup_gaps', 'lost_deal_notes'],
    disqualifying_signals: ['no_sales_process', 'no_crm_access'],
    deliverables: ['pipeline_audit', 'bottleneck_report', 'remediation_plan', '30_day_roadmap'],
    implementation_window: '7-10 business days',
    pricing_model: 'fixed',
    price_floor: 500,
    price_ceiling: 1500,
    proof_assets: [],
    prerequisites: ['crm_export', 'opportunity_list', 'sales_stage_definitions'],
    outcome_metrics: ['response_time', 'proposal_throughput', 'pipeline_hygiene_score'],
    fulfillment_playbook_id: 'revenue_leak_diagnostic_v1',
    checkout_product_id: undefined,
    approval_requirements: ['human_review_before_outreach']
  },
  {
    offer_id: 'verified_deal_desk_sprint',
    name: 'Verified Deal Desk Sprint',
    target_pains: ['slow_proposals', 'weak_discovery', 'poor_followup'],
    target_buyer_roles: ['founder', 'head_of_sales', 'sales_manager'],
    required_signals: ['high_value_deals', 'proposal_bottleneck', 'followup_inconsistency'],
    disqualifying_signals: ['no_decision_maker_access', 'no_product_catalog'],
    deliverables: ['account_briefs', 'proposal_system', 'followup_routing', 'dashboard'],
    implementation_window: '10-14 business days',
    pricing_model: 'fixed',
    price_floor: 3500,
    price_ceiling: 10000,
    proof_assets: [],
    prerequisites: ['crm_access', 'product_catalog', 'proposal_templates'],
    outcome_metrics: ['proposal_cycle_time', 'win_rate', 'deal_velocity'],
    fulfillment_playbook_id: 'deal_desk_sprint_v1',
    checkout_product_id: undefined,
    approval_requirements: ['human_review_before_outreach', 'scope_signoff']
  },
  {
    offer_id: 'churn_intervention_audit',
    name: 'Churn Intervention Audit',
    target_pains: ['silent_disengagement', 'cancellation_risk', 'low_adoption'],
    target_buyer_roles: ['founder', 'head_of_customer_success', 'operations'],
    required_signals: ['usage_decline', 'support_tickets', 'cancellation_requests'],
    disqualifying_signals: ['no_usage_data', 'no_customer_contact'],
    deliverables: ['churn_risk_scores', 'retention_playbooks', 'alert_system'],
    implementation_window: '7-14 business days',
    pricing_model: 'fixed',
    price_floor: 750,
    price_ceiling: 2000,
    proof_assets: [],
    prerequisites: ['usage_data', 'customer_list', 'subscription_data'],
    outcome_metrics: ['churn_rate', 'retention_rate', 'intervention_success_rate'],
    fulfillment_playbook_id: 'churn_audit_v1',
    checkout_product_id: undefined,
    approval_requirements: ['human_review_before_outreach']
  }
];

export interface OfferDefinition {
  offer_id: string;
  name: string;
  target_pains: string[];
  target_buyer_roles: string[];
  required_signals: string[];
  disqualifying_signals: string[];
  deliverables: string[];
  implementation_window: string;
  pricing_model: 'fixed' | 'subscription' | 'usage' | 'hybrid';
  price_floor?: number;
  price_ceiling?: number;
  proof_assets: string[];
  prerequisites: string[];
  outcome_metrics: string[];
  fulfillment_playbook_id: string;
  checkout_product_id?: string;
  approval_requirements: string[];
}
