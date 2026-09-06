import { OFFER_REGISTRY, type OfferDefinition } from './config.js';
import type { PainPointCase } from './types.js';

export function matchOffers(painCases: PainPointCase[]): (PainPointCase & { matched_solution: NonNullable<PainPointCase['matched_solution']> })[] {
  return painCases.map(caseData => {
    let bestMatch: OfferDefinition | null = null;
    let bestScore = 0;
    for (const offer of OFFER_REGISTRY) {
      let score = 0;
      for (const pain of caseData.root_cause_hypotheses) {
        if (offer.target_pains.some(tp => pain.hypothesis.toLowerCase().includes(tp) || tp.includes(pain.hypothesis.toLowerCase()))) {
          score += 0.3;
        }
      }
      if (caseData.symptom.toLowerCase().includes('payment') && offer.target_pains.some(tp => tp.includes('payment') || tp.includes('churn'))) score += 0.2;
      if (caseData.symptom.toLowerCase().includes('deal') && offer.target_pains.some(tp => tp.includes('deal') || tp.includes('proposal'))) score += 0.2;
      if (caseData.symptom.toLowerCase().includes('churn') && offer.target_pains.some(tp => tp.includes('churn') || tp.includes('cancellation'))) score += 0.2;
      if (score > bestScore) {
        bestScore = score;
        bestMatch = offer;
      }
    }
    if (bestMatch && bestScore > 0.3) {
      return {
        ...caseData,
        matched_solution: {
          offer_id: bestMatch.offer_id,
          offer_name: bestMatch.name,
          match_score: bestScore
        },
        readiness_state: 'solution_matched',
        next_best_action: { action_type: 'create_brief', description: `Generate brief for ${bestMatch.name}`, requires_approval: false }
      };
    }
    return caseData;
  }).filter(c => c.matched_solution);
}
