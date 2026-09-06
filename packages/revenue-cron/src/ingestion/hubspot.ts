import { Client } from '@hubspot/api-client';
import { loadEnv } from '../config.js';
import type { EvidenceReference } from '../types.js';

let hubspotClient: Client | null = null;

function getHubSpot(): Client {
  if (!hubspotClient) {
    const { HUBSPOT_ACCESS_TOKEN } = loadEnv();
    if (!HUBSPOT_ACCESS_TOKEN) throw new Error('HUBSPOT_ACCESS_TOKEN not configured');
    hubspotClient = new Client({ accessToken: HUBSPOT_ACCESS_TOKEN });
  }
  return hubspotClient;
}
export async function fetchDeals(limit = 100): Promise<EvidenceReference[]> {
  const hubspot = getHubSpot();
  const deals = await hubspot.crm.deals.basicApi.getPage(limit);
  return deals.results.map(d => ({
    evidence_id: `hs_deal_${d.id}`,
    source_system: 'hubspot',
    source_locator: `deal/${d.id}`,
    content_hash: '',
    observed_at: d.properties.hs_lastmodifieddate || new Date().toISOString(),
    retrieved_at: new Date().toISOString(),
    authority_level: 'internal_system' as const,
    freshness_score: 0.9,
    integrity_status: 'verified' as const
  }));
}
export async function fetchContacts(limit = 100): Promise<EvidenceReference[]> {
  const hubspot = getHubSpot();
  const contacts = await hubspot.crm.contacts.basicApi.getPage(limit);
  return contacts.results.map(c => ({
    evidence_id: `hs_contact_${c.id}`,
    source_system: 'hubspot',
    source_locator: `contact/${c.id}`,
    content_hash: '',
    observed_at: c.properties.hs_lastmodifieddate || new Date().toISOString(),
    retrieved_at: new Date().toISOString(),
    authority_level: 'internal_system' as const,
    freshness_score: 0.9,
    integrity_status: 'verified' as const
  }));
}
export async function fetchCompanies(limit = 100): Promise<EvidenceReference[]> {
  const hubspot = getHubSpot();
  const companies = await hubspot.crm.companies.basicApi.getPage(limit);
  return companies.results.map(c => ({
    evidence_id: `hs_company_${c.id}`,
    source_system: 'hubspot',
    source_locator: `company/${c.id}`,
    content_hash: '',
    observed_at: c.properties.hs_lastmodifieddate || new Date().toISOString(),
    retrieved_at: new Date().toISOString(),
    authority_level: 'internal_system' as const,
    freshness_score: 0.9,
    integrity_status: 'verified' as const
  }));
}
