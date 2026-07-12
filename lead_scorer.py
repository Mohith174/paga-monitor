#!/usr/bin/env python3
"""
Simple lead scoring algorithm - no AI/API required
Scores based on heuristics: employee count, violation keywords, case age
"""
from database import Database
from datetime import datetime
import re

class LeadScorer:
    """Heuristic-based lead scoring (no AI required)"""
    
    # Violation severity weights
    VIOLATIONS = {
        'overtime': 3,
        'meal break': 2,
        'rest break': 2,
        'minimum wage': 4,
        'wage statement': 2,
        'waiting time': 3,
        'unpaid': 4,
        'misclassification': 5,
        'retaliation': 3
    }
    
    def score_lead(self, case: dict) -> dict:
        """
        Calculate lead score (0-100) based on:
        - Employee count (higher = more valuable)
        - Violation types (from case fields)
        - Case freshness (newer = better)
        """
        score = 0
        factors = []
        
        # 1. Employee Count (0-40 points)
        num_employees = case.get('num_employees') or 0
        if num_employees >= 1000:
            score += 40
            factors.append('Large class (1000+)')
        elif num_employees >= 500:
            score += 30
            factors.append('Large class (500+)')
        elif num_employees >= 200:
            score += 25
            factors.append('Medium class (200+)')
        elif num_employees >= 100:
            score += 20
            factors.append('Medium class (100+)')
        elif num_employees >= 50:
            score += 15
            factors.append('Small-medium class')
        else:
            score += 10
            factors.append('Small class')
        
        # 2. Violation Types (0-40 points)
        # Extract from submission_type and submission_name
        text = f"{case.get('submission_type', '')} {case.get('submission_name', '')}".lower()
        
        violation_score = 0
        identified_violations = []
        for keyword, weight in self.VIOLATIONS.items():
            if keyword in text:
                violation_score += weight
                identified_violations.append(keyword.title())
        
        # Cap at 40 points
        score += min(40, violation_score * 3)
        
        if identified_violations:
            factors.append(f"Violations: {', '.join(identified_violations)}")
        
        # 3. Case Freshness (0-20 points)
        submission_date = case.get('submission_date')
        if submission_date:
            try:
                filed = datetime.strptime(submission_date, '%Y-%m-%d')
                days_old = (datetime.now() - filed).days
                
                if days_old <= 7:
                    score += 20
                    factors.append('Very fresh (<7 days)')
                elif days_old <= 30:
                    score += 15
                    factors.append('Recent (<30 days)')
                elif days_old <= 90:
                    score += 10
                    factors.append('Moderately fresh')
                else:
                    score += 5
            except:
                pass
        
        # Normalize to 0-100
        score = min(100, score)
        
        # Determine priority
        if score >= 70:
            priority = 'High'
        elif score >= 50:
            priority = 'Medium'
        else:
            priority = 'Low'
        
        return {
            'score': score,
            'priority': priority,
            'factors': factors,
            'violations': identified_violations if identified_violations else ['General PAGA']
        }

def score_all_unscored(batch_size=100):
    """Score all cases that don't have analysis yet, in batches"""
    db = Database()
    scorer = LeadScorer()
    total_scored = 0

    while True:
        with db.get_conn() as conn:
            cases = conn.execute("""
                SELECT id, lwda_number, employer_name, submission_type, submission_name,
                       submission_date, num_employees, status
                FROM cases
                WHERE id NOT IN (SELECT case_id FROM case_analysis)
                LIMIT ?
            """, (batch_size,)).fetchall()

        if not cases:
            break

        print(f"Scoring {len(cases)} cases...")

        for case in cases:
            case_dict = dict(case)
            result = scorer.score_lead(case_dict)

            # Store in analysis table
            analysis_data = {
                'agent_score': result['score'],
                'priority': result['priority'],
                'violations': result['violations'],
                'estimated_class_size': case_dict.get('num_employees', 0),
                'analysis_notes': ' | '.join(result['factors'])
            }
            
            db.add_analysis(case_dict['id'], analysis_data)

            if result['score'] >= 70:
                print(f"  ⭐ {case_dict['lwda_number']} ({case_dict['employer_name']}): {result['score']}")

        total_scored += len(cases)

    print(f"\n✅ Scored {total_scored} leads")

if __name__ == '__main__':
    score_all_unscored()
