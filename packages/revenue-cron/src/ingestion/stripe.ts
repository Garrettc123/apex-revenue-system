import Stripe from 'stripe';
import { loadEnv } from '../config.js';
import type { EvidenceReference } from '../types.js';

let stripeClient: Stripe | null = null;

function getStripe(): Stripe {
  if (!stripeClient) {
    const { STRIPE_SECRET_KEY } = loadEnv();
    if (!STRIPE_SECRET_KEY) throw new Error('STRIPE_SECRET_KEY not configured');
    stripeClient = new Stripe(STRIPE_SECRET_KEY, { apiVersion: '2024-06-20' });
  }
  return stripeClient;
}
export async function fetchRecentPayments(limit = 100): Promise<EvidenceReference[]> {
  const stripe = getStripe();
  const payments = await stripe.paymentIntents.list({ limit, expand: ['data.customer'] });
  return payments.data.map(p => ({
    evidence_id: `stripe_pi_${p.id}`,
    source_system: 'stripe',
    source_locator: `payment_intent/${p.id}`,
    content_hash: '',
    observed_at: new Date(p.created * 1000).toISOString(),
    retrieved_at: new Date().toISOString(),
    authority_level: 'internal_system' as const,
    freshness_score: 1.0,
    integrity_status: 'verified' as const
  }));
}
export async function fetchSubscriptions(limit = 100): Promise<EvidenceReference[]> {
  const stripe = getStripe();
  const subs = await stripe.subscriptions.list({ limit, status: 'all' });
  return subs.data.map(s => ({
    evidence_id: `stripe_sub_${s.id}`,
    source_system: 'stripe',
    source_locator: `subscription/${s.id}`,
    content_hash: '',
    observed_at: new Date(s.created * 1000).toISOString(),
    retrieved_at: new Date().toISOString(),
    authority_level: 'internal_system' as const,
    freshness_score: 1.0,
    integrity_status: 'verified' as const
  }));
}
export async function fetchFailedPayments(limit = 50): Promise<EvidenceReference[]> {
  const stripe = getStripe();
  const payments = await stripe.paymentIntents.list({ limit, status: 'failed' });
  return payments.data.map(p => ({
    evidence_id: `stripe_failed_${p.id}`,
    source_system: 'stripe',
    source_locator: `failed_payment/${p.id}`,
    content_hash: '',
    observed_at: new Date(p.created * 1000).toISOString(),
    retrieved_at: new Date().toISOString(),
    authority_level: 'internal_system' as const,
    freshness_score: 1.0,
    integrity_status: 'verified' as const
  }));
}
