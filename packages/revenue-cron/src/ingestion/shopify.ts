import { loadEnv } from '../config.js';
import type { EvidenceReference } from '../types.js';

async function shopifyFetch<T>(path: string): Promise<T> {
  const { SHOPIFY_ACCESS_TOKEN } = loadEnv();
  if (!SHOPIFY_ACCESS_TOKEN) throw new Error('SHOPIFY_ACCESS_TOKEN not configured');
  const store = process.env.SHOPIFY_STORE_DOMAIN;
  if (!store) throw new Error('SHOPIFY_STORE_DOMAIN not configured');
  const res = await fetch(`https://${store}/admin/api/2024-07${path}`, {
    headers: { 'X-Shopify-Access-Token': SHOPIFY_ACCESS_TOKEN, 'Content-Type': 'application/json' }
  });
  if (!res.ok) throw new Error(`Shopify ${path} failed: ${res.statusText}`);
  return res.json() as Promise<T>;
}
export async function fetchOrders(limit = 100): Promise<EvidenceReference[]> {
  const data = await shopifyFetch<{ orders: any[] }>(`/orders.json?limit=${limit}&status=any`);
  return data.orders.map(o => ({
    evidence_id: `shopify_order_${o.id}`,
    source_system: 'shopify',
    source_locator: `order/${o.id}`,
    content_hash: '',
    observed_at: o.created_at,
    retrieved_at: new Date().toISOString(),
    authority_level: 'internal_system' as const,
    freshness_score: 0.9,
    integrity_status: 'verified' as const
  }));
}
export async function fetchCustomers(limit = 100): Promise<EvidenceReference[]> {
  const data = await shopifyFetch<{ customers: any[] }>(`/customers.json?limit=${limit}`);
  return data.customers.map(c => ({
    evidence_id: `shopify_customer_${c.id}`,
    source_system: 'shopify',
    source_locator: `customer/${c.id}`,
    content_hash: '',
    observed_at: c.created_at,
    retrieved_at: new Date().toISOString(),
    authority_level: 'internal_system' as const,
    freshness_score: 0.9,
    integrity_status: 'verified' as const
  }));
}
