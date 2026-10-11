# apex_agent_deep.py — GARCAR APEX deep walking skeleton
# RHNS recursive governance + Titan-style continuous state + mastery ladder + skill transfer

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple
import hashlib
import json
import time
import math


@dataclass
class GoalNode:
    id: str
    description: str
    constraints: List[str] = field(default_factory=list)
    children: List['GoalNode'] = field(default_factory=list)
    status: str = 'pending'
    outcome: Optional[bool] = None


class ConstraintEngine:
    def __init__(self, rules: List[str]):
        self.rules = rules

    def check(self, action: str, context: Dict[str, Any]) -> Tuple[bool, str]:
        if context.get('kill'):
            return False, 'kill switch active'
        if action == 'spend' and context.get('spend', 0) > context.get('spend_cap', 0):
            return False, 'spend exceeds cap'
        if action == 'first_contact' and not context.get('human_approval', False):
            return False, 'human approval required for first contact'
        if action == 'act' and context.get('competence', 0) < 0.4:
            return False, 'competence below act threshold (0.4)'
        return True, 'ok'


class StateVector:
    def __init__(self):
        self.values = {
            'energy': 1.0, 'attention': 0.7, 'confidence': 0.5,
            'domain_familiarity': 0.0, 'risk': 0.1, 'outcome_valence': 0.5,
            'dopamine': 0.5, 'serotonin': 0.5, 'norepinephrine': 0.5,
            'acetylcholine': 0.5, 'endorphin': 0.5, 'gaba': 0.5,
        }

    def update_from_outcome(self, success: bool, competence: float):
        delta = 0.05 if success else -0.03
        self.values['confidence'] = max(0.0, min(1.0, self.values['confidence'] + delta))
        self.values['domain_familiarity'] = competence
        self.values['dopamine'] = max(0.0, min(1.0, self.values['dopamine'] + (0.08 if success else -0.05)))
        self.values['outcome_valence'] = 0.7 if success else 0.3

    def homeostasis_needed(self):
        return any(abs(v - 0.5) > 0.35 for v in self.values.values())


MASTERY_LEVELS = [(0.0, 'Novice'), (0.2, 'Apprentice'), (0.4, 'Competent'), (0.6, 'Proficient'), (0.8, 'Expert')]

def mastery_label(competence: float) -> str:
    label = 'Novice'
    for t, n in MASTERY_LEVELS:
        if competence >= t:
            label = n
    return label


@dataclass
class Skill:
    name: str
    schema: Dict[str, Any]
    competence: float = 0.0
    evidence: List[str] = field(default_factory=list)
    parent: Optional[str] = None


class ExpertiseEngine:
    def __init__(self):
        self.skills = {}
        self.ledger = []
        self._last_hash = 'genesis'

    def _chain(self, payload):
        blob = json.dumps({'prev': self._last_hash, **payload}, sort_keys=True)
        digest = hashlib.sha256(blob.encode()).hexdigest()
        self._last_hash = digest
        return digest

    def synthesize(self, trace, parent_skill=None):
        domain = str(trace.get('domain', 'unknown'))
        name = f'{domain}_skill'
        schema = {'inputs': trace.get('inputs'), 'steps': trace.get('steps'), 'success_criteria': trace.get('success'), 'failure_modes': trace.get('failures', [])}
        skill = self.skills.get(name)
        if skill is None:
            seed = 0.0
            if parent_skill and parent_skill in self.skills:
                seed = self.skills[parent_skill].competence * 0.3
            skill = Skill(name, schema, competence=seed, parent=parent_skill)
        else:
            skill.schema = schema
        success = bool(trace.get('success', False))
        delta = 0.12 if success else 0.02
        skill.competence = min(1.0, skill.competence + delta)
        digest = self._chain({'event': 'skill_update', 'skill': name, 'competence': skill.competence, 'success': success})
        skill.evidence.append(digest[:16])
        self.skills[name] = skill
        self.ledger.append({'ts': time.time(), 'event': 'skill_update', 'skill': name, 'competence': round(skill.competence, 3), 'mastery': mastery_label(skill.competence), 'hash': digest, 'parent': parent_skill})
        return skill

    def sovereignty_score(self, e, v):
        return 0.7 * e + 0.3 * v


class ApexAgent:
    def __init__(self):
        self.goal_tree = None
        self.expertise = ExpertiseEngine()
        self.state = StateVector()
        self.constraints = ConstraintEngine(['no_spend_without_approval', 'human_approval_for_first_contact', 'audit_log_required', 'competence_threshold_for_autonomy'])
        self.kill = False
        self.spend_cap = 0.0
        self.autonomy = 'draft'

    def decompose(self, objective):
        root = GoalNode(id='root', description=objective)
        root.children = [GoalNode(id='perceive', description='Perceive'), GoalNode(id='plan', description='Plan'), GoalNode(id='act', description='Act'), GoalNode(id='evaluate', description='Evaluate'), GoalNode(id='synthesize', description='Synthesize')]
        self.goal_tree = root
        return root

    def encounter(self, domain, payload, success=True, parent_domain=None):
        if self.kill:
            return {'status': 'killed'}
        context = {'kill': self.kill, 'spend': 0, 'spend_cap': self.spend_cap, 'human_approval': self.autonomy != 'act', 'competence': self.expertise.skills.get(f'{domain}_skill', Skill('', {})).competence}
        allowed, reason = self.constraints.check('act', context)
        if not allowed and self.autonomy == 'act':
            return {'status': 'blocked', 'reason': reason}
        self.decompose(f'Become expert at {domain}')
        parent_skill = f'{parent_domain}_skill' if parent_domain else None
        trace = {'domain': domain, 'inputs': payload, 'steps': ['perceive', 'plan', 'act', 'evaluate', 'synthesize'], 'success': success, 'failures': [] if success else ['outcome_negative']}
        skill = self.expertise.synthesize(trace, parent_skill=parent_skill)
        self.state.update_from_outcome(success, skill.competence)
        s_score = self.expertise.sovereignty_score(skill.competence, 1.0 if success else 0.3)
        return {'status': 'expert_update', 'skill': skill.name, 'competence': round(skill.competence, 3), 'mastery': mastery_label(skill.competence), 'sovereignty': round(s_score, 3), 'ledger_hash': self.expertise.ledger[-1]['hash'][:16] if self.expertise.ledger else None, 'constraints_ok': allowed}

    def report(self):
        return {'skills': {k: {'competence': round(v.competence, 3), 'mastery': mastery_label(v.competence), 'evidence': len(v.evidence), 'parent': v.parent} for k, v in self.expertise.skills.items()}, 'ledger_entries': len(self.expertise.ledger), 'last_hash': self.expertise._last_hash[:16], 'autonomy': self.autonomy, 'kill': self.kill}

    def kill_switch(self):
        self.kill = True
