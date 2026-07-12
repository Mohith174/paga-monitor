#!/usr/bin/env python3
"""
Postgres database with analytics for PAGA leads
"""

import os
import json
import psycopg2
import psycopg2.extras
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Tuple
from contextlib import contextmanager


class _ConnWrapper:
    """Thin wrapper so call sites can use conn.execute(...) like sqlite3 did"""

    def __init__(self, conn):
        self._conn = conn

    def execute(self, sql, params=None):
        cur = self._conn.cursor()
        cur.execute(sql, params or ())
        return cur

    def executescript(self, sql):
        self._conn.cursor().execute(sql)

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()

    def close(self):
        self._conn.close()


class Database:
    """Postgres database manager with analytics"""

    def __init__(self, dsn=None):
        self.dsn = dsn or os.environ["DATABASE_URL"]
        self.init_schema()

    @contextmanager
    def get_conn(self):
        """Context manager for database connections"""
        conn = psycopg2.connect(self.dsn, cursor_factory=psycopg2.extras.RealDictCursor)
        wrapper = _ConnWrapper(conn)
        try:
            yield wrapper
            wrapper.commit()
        except Exception as e:
            wrapper.rollback()
            raise e
        finally:
            wrapper.close()

    def init_schema(self):
        """Initialize database schema"""
        with self.get_conn() as conn:
            conn.executescript("""
                -- Cases table
                CREATE TABLE IF NOT EXISTS cases (
                    id SERIAL PRIMARY KEY,
                    lwda_number TEXT UNIQUE NOT NULL,
                    submission_name TEXT,
                    submission_type TEXT,
                    submission_date DATE,
                    employer_name TEXT,
                    employer_city TEXT,
                    employer_zip TEXT,
                    num_employees INTEGER,
                    law_firm TEXT,
                    attorney_name TEXT,
                    plaintiff_name TEXT,
                    court_case_number TEXT,
                    pdf_url TEXT,
                    detail_url TEXT,
                    industry_sector TEXT,
                    estimated_value INTEGER,
                    content_hash TEXT,
                    version INTEGER DEFAULT 1,
                    first_seen TIMESTAMP,
                    last_seen TIMESTAMP,
                    status TEXT DEFAULT 'new',
                    priority INTEGER DEFAULT 0,
                    assigned_to TEXT,
                    contacted_date TIMESTAMP,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );

                -- Scrape runs
                CREATE TABLE IF NOT EXISTS runs (
                    id SERIAL PRIMARY KEY,
                    started TIMESTAMP,
                    completed TIMESTAMP,
                    duration REAL,
                    total_found INTEGER,
                    new_cases INTEGER,
                    amended_cases INTEGER,
                    duplicates INTEGER,
                    status TEXT,
                    error TEXT
                );

                -- User notes
                CREATE TABLE IF NOT EXISTS notes (
                    id SERIAL PRIMARY KEY,
                    case_id INTEGER,
                    "user" TEXT,
                    note TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (case_id) REFERENCES cases(id)
                );

                -- Activity log
                CREATE TABLE IF NOT EXISTS activity_log (
                    id SERIAL PRIMARY KEY,
                    case_id INTEGER,
                    action TEXT,
                    details TEXT,
                    "user" TEXT,
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (case_id) REFERENCES cases(id)
                );

                -- Analysis results
                CREATE TABLE IF NOT EXISTS case_analysis (
                    id SERIAL PRIMARY KEY,
                    case_id INTEGER,
                    raw_text TEXT,
                    summary TEXT,
                    violations TEXT,  -- JSON list
                    class_size_estimate INTEGER,
                    plaintiff_counsel TEXT,
                    agent_score INTEGER,
                    processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (case_id) REFERENCES cases(id)
                );

                -- Indexes
                CREATE INDEX IF NOT EXISTS idx_employer_name ON cases(employer_name);
                CREATE INDEX IF NOT EXISTS idx_submission_date ON cases(submission_date DESC);
                CREATE INDEX IF NOT EXISTS idx_status ON cases(status);
                CREATE INDEX IF NOT EXISTS idx_first_seen ON cases(first_seen DESC);
                CREATE INDEX IF NOT EXISTS idx_city ON cases(employer_city);
                CREATE INDEX IF NOT EXISTS idx_law_firm ON cases(law_firm);
                CREATE INDEX IF NOT EXISTS idx_hash ON cases(content_hash);
            """)

    def process_case(self, case: Dict) -> Tuple[str, int]:
        """
        Process a case: insert new, update amended, or skip duplicate
        Returns: (action, case_id) where action is 'new', 'amended', or 'duplicate'
        """
        with self.get_conn() as conn:
            # Check if exists
            existing = conn.execute(
                "SELECT id, content_hash, version FROM cases WHERE lwda_number = %s",
                (case['lwda_number'],)
            ).fetchone()

            if not existing:
                # NEW CASE
                cursor = conn.execute("""
                    INSERT INTO cases (
                        lwda_number, submission_name, submission_type, submission_date,
                        employer_name, employer_city, employer_zip, num_employees,
                        pdf_url, detail_url, content_hash, first_seen, last_seen,
                        industry_sector
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING id
                """, (
                    case['lwda_number'], case.get('submission_name'), case['submission_type'],
                    case['submission_date'], case['employer_name'], case.get('employer_city'),
                    case.get('employer_zip'), case.get('num_employees'), case.get('pdf_url'),
                    case.get('detail_url'), case['content_hash'], datetime.now(), datetime.now(),
                    self._infer_industry(case['employer_name'])
                ))
                return 'new', cursor.fetchone()['id']

            elif existing['content_hash'] != case['content_hash']:
                # AMENDED CASE
                new_version = existing['version'] + 1
                conn.execute("""
                    UPDATE cases SET
                        submission_name=%s, submission_type=%s, submission_date=%s,
                        employer_name=%s, employer_city=%s, employer_zip=%s, num_employees=%s,
                        pdf_url=%s, content_hash=%s, version=%s, last_seen=%s, updated_at=%s,
                        status='new'
                    WHERE lwda_number=%s
                """, (
                    case.get('submission_name'), case['submission_type'], case['submission_date'],
                    case['employer_name'], case.get('employer_city'), case.get('employer_zip'),
                    case.get('num_employees'), case.get('pdf_url'), case['content_hash'],
                    new_version, datetime.now(), datetime.now(), case['lwda_number']
                ))
                return 'amended', existing['id']

            else:
                # DUPLICATE
                conn.execute(
                    "UPDATE cases SET last_seen=%s WHERE lwda_number=%s",
                    (datetime.now(), case['lwda_number'])
                )
                return 'duplicate', existing['id']

    def _infer_industry(self, employer_name: str) -> Optional[str]:
        """Infer industry from employer name"""
        name = employer_name.lower()

        # Retail & Consumer
        if any(word in name for word in ['walmart', 'target', 'costco', 'safeway', 'cvs', 'walgreens', 'dollar', 'store', 'shop', 'market', 'retail', 'fashion', 'apparel', 'gap', 'old navy']):
            return 'Retail'
        # Food Service
        elif any(word in name for word in ['restaurant', 'grill', 'cafe', 'kitchen', 'bar', 'pizza', 'burger', 'food', 'dining', 'mcdonald', 'starbucks', 'chipotle', 'subway']):
            return 'Restaurant/Food Service'
        # Hospitality
        elif any(word in name for word in ['hotel', 'motel', 'inn', 'resort', 'marriott', 'hilton', 'hyatt', 'casino']):
            return 'Hospitality'
        # Healthcare
        elif any(word in name for word in ['healthcare', 'medical', 'hospital', 'clinic', 'care', 'health', 'kaiser', 'nursing', 'pharmacy']):
            return 'Healthcare'
        # Manufacturing
        elif any(word in name for word in ['manufacturing', 'industries', 'factory', 'production', 'industrial', 'assembly']):
            return 'Manufacturing'
        # Transportation/Logistics
        elif any(word in name for word in ['trucking', 'logistics', 'transport', 'delivery', 'freight', 'shipping', 'courier', 'fedex', 'ups', 'schneider']):
            return 'Transportation/Logistics'
        # Construction
        elif any(word in name for word in ['construction', 'builder', 'contractor', 'building', 'remodeling']):
            return 'Construction'
        # Services (Professional)
        elif any(word in name for word in ['cleaning', 'janitorial', 'maintenance', 'security', 'staffing', 'services', 'solutions', 'consulting', 'home care']):
            return 'Services'
        # Tech/Software
        elif any(word in name for word in ['tech', 'software', 'systems', 'digital', 'data', 'cloud', 'cyber']):
            return 'Technology'
        # Entertainment/Media
        elif any(word in name for word in ['studios', 'entertainment', 'media', 'production', 'theater', 'cinema', 'universal']):
            return 'Entertainment/Media'
        # Energy/Utilities
        elif any(word in name for word in ['energy', 'electric',  'gas', 'utility', 'power', 'solar']):
            return 'Energy/Utilities'
        # Agriculture
        elif any(word in name for word in ['farm', 'agriculture', 'growers', 'ranch', 'vineyard', 'dairy', 'juice', 'packing', 'nut']):
            return 'Agriculture'
        # Finance/Insurance
        elif any(word in name for word in ['bank', 'financial', 'insurance', 'credit', 'investment']):
            return 'Finance/Insurance'
        # Legal/Professional Services
        elif any(word in name for word in ['law', 'legal', 'attorney', 'llp', 'associates', 'group', 'partners']):
            return 'Professional Services'
        # Automotive
        elif any(word in name for word in ['auto', 'car', 'automotive', 'chevrolet', 'ford', 'toyota', 'dealership', 'chevron', 'shell']):
            return 'Automotive'
        # Default
        else:
            return 'Other'

    def get_dashboard_stats(self) -> Dict:
        """Get KPIs for dashboard"""
        with self.get_conn() as conn:
            today = datetime.now().date()
            week_ago = today - timedelta(days=7)
            month_ago = today - timedelta(days=30)

            stats = {}

            # Cases filed today
            stats['new_today'] = conn.execute(
                "SELECT COUNT(*) as n FROM cases WHERE submission_date = %s", (today,)
            ).fetchone()['n']

            # Cases filed this week
            stats['new_week'] = conn.execute(
                "SELECT COUNT(*) as n FROM cases WHERE submission_date >= %s", (week_ago,)
            ).fetchone()['n']

            # Cases filed this month
            stats['new_month'] = conn.execute(
                "SELECT COUNT(*) as n FROM cases WHERE submission_date >= %s", (month_ago,)
            ).fetchone()['n']

            # Lead status counts
            for status in ['new', 'contacted', 'qualified', 'closed']:
                stats[f'status_{status}'] = conn.execute(
                    "SELECT COUNT(*) as n FROM cases WHERE status = %s", (status,)
                ).fetchone()['n']

            # Average response time (hours from first_seen to contacted_date)
            avg_response = conn.execute("""
                SELECT AVG(EXTRACT(EPOCH FROM (contacted_date - first_seen)) / 3600) as avg_hours
                FROM cases WHERE contacted_date IS NOT NULL
            """).fetchone()['avg_hours']
            stats['avg_response_hours'] = round(avg_response, 1) if avg_response else 0

            return stats

    def get_filing_trends(self, days=30) -> List[Dict]:
        """Get daily filing counts for trend chart"""
        with self.get_conn() as conn:
            start_date = datetime.now().date() - timedelta(days=days)

            rows = conn.execute("""
                SELECT submission_date as date, COUNT(*) as count
                FROM cases
                WHERE submission_date >= %s
                GROUP BY submission_date
                ORDER BY date
            """, (start_date,)).fetchall()

            return [{'date': str(r['date']), 'count': r['count']} for r in rows]

    def get_top_cities(self, limit=10) -> List[Dict]:
        """Get top cities by case count"""
        with self.get_conn() as conn:
            rows = conn.execute("""
                SELECT employer_city, COUNT(*) as count
                FROM cases
                WHERE employer_city IS NOT NULL AND employer_city != ''
                GROUP BY employer_city
                ORDER BY count DESC
                LIMIT %s
            """, (limit,)).fetchall()

            return [{'city': r['employer_city'], 'count': r['count']} for r in rows]

    def get_industry_breakdown(self) -> List[Dict]:
        """Get case count by industry"""
        with self.get_conn() as conn:
            rows = conn.execute("""
                SELECT industry_sector, COUNT(*) as count
                FROM cases
                WHERE industry_sector IS NOT NULL
                GROUP BY industry_sector
                ORDER BY count DESC
            """).fetchall()

            return [{'industry': r['industry_sector'], 'count': r['count']} for r in rows]

    def get_top_employers(self, min_cases=2, limit=10) -> List[Dict]:
        """Get repeat offender employers"""
        with self.get_conn() as conn:
            rows = conn.execute("""
                SELECT employer_name, COUNT(*) as count
                FROM cases
                GROUP BY employer_name
                HAVING COUNT(*) >= %s
                ORDER BY count DESC
                LIMIT %s
            """, (min_cases, limit)).fetchall()

            return [{'employer': r['employer_name'], 'count': r['count']} for r in rows]

    def get_recent_cases(self, limit=50, status=None, min_priority=0) -> List[Dict]:
        """Get recent cases with optional status filter"""
        with self.get_conn() as conn:
            query = "SELECT * FROM cases WHERE 1=1"
            params = []

            if status:
                query += " AND status = %s"
                params.append(status)

            if min_priority > 0:
                query += " AND priority >= %s"
                params.append(min_priority)

            query += " ORDER BY submission_date DESC, first_seen DESC LIMIT %s"
            params.append(limit)

            rows = conn.execute(query, params).fetchall()
            return [dict(row) for row in rows]

    def update_case_status(self, case_id: int, status: str, user: str = 'system'):
        """Update case status and log activity"""
        with self.get_conn() as conn:
            # Update status
            conn.execute(
                "UPDATE cases SET status=%s, updated_at=%s WHERE id=%s",
                (status, datetime.now(), case_id)
            )

            # Log activity
            conn.execute("""
                INSERT INTO activity_log (case_id, action, "user")
                VALUES (%s, %s, %s)
            """, (case_id, f'Status changed to {status}', user))

    def add_note(self, case_id: int, note: str, user: str = 'user'):
        """Add note to a case"""
        with self.get_conn() as conn:
            conn.execute("""
                INSERT INTO notes (case_id, note, "user")
                VALUES (%s, %s, %s)
            """, (case_id, note, user))

    def get_case_analysis(self, case_id: int) -> Optional[Dict]:
        """Get analysis for a case"""
        with self.get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM case_analysis WHERE case_id = %s",
                (case_id,)
            ).fetchone()
            if row:
                d = dict(row)
                # Parse violations JSON if present
                if d.get('violations'):
                    try:
                        d['violations'] = json.loads(d['violations'])
                    except:
                        d['violations'] = []
                return d
            return None

    def add_analysis(self, case_id: int, analysis: Dict):
        """Store analysis results"""
        with self.get_conn() as conn:
            conn.execute("""
                INSERT INTO case_analysis (
                    case_id, raw_text, summary, violations,
                    class_size_estimate, plaintiff_counsel, agent_score
                ) VALUES (%s, %s, %s, %s, %s, %s, %s)
            """, (
                case_id,
                analysis.get('raw_text', '')[:10000],  # Truncate raw text
                analysis.get('summary', ''),
                json.dumps(analysis.get('violations', [])),
                analysis.get('class_size_estimate'),
                analysis.get('plaintiff_counsel', ''),
                analysis.get('agent_score', 0)
            ))

            # Update priority if high score
            if analysis.get('agent_score', 0) > 50:
                conn.execute(
                    "UPDATE cases SET priority = 10, status = 'qualified' WHERE id = %s",
                    (case_id,)
                )

    def get_unanalyzed_cases(self, limit=10) -> List[Dict]:
        """Get cases that need analysis (have PDF but no analysis record)"""
        with self.get_conn() as conn:
            rows = conn.execute("""
                SELECT c.* FROM cases c
                LEFT JOIN case_analysis a ON c.id = a.case_id
                WHERE a.id IS NULL
                AND c.pdf_url IS NOT NULL
                AND c.pdf_url != ''
                ORDER BY c.first_seen DESC
                LIMIT %s
            """, (limit,)).fetchall()
            return [dict(r) for r in rows]

    def get_priority_leads(self, limit=50) -> List[Dict]:
        """Get high priority leads with analysis"""
        with self.get_conn() as conn:
            rows = conn.execute("""
                SELECT c.*, a.agent_score, a.violations, a.summary
                FROM cases c
                JOIN case_analysis a ON c.id = a.case_id
                WHERE a.agent_score >= 70
                ORDER BY a.agent_score DESC, c.submission_date DESC
                LIMIT %s
            """, (limit,)).fetchall()

            results = []
            for r in rows:
                d = dict(r)
                if d.get('violations'):
                    try:
                        d['violations'] = json.loads(d['violations'])
                    except:
                        d['violations'] = []
                results.append(d)
            return results

    def log_run(self, started, duration, total, new, amended, duplicates, status='ok', error=None):
        """Log a scrape run"""
        with self.get_conn() as conn:
            conn.execute("""
                INSERT INTO runs (started, completed, duration, total_found, new_cases,
                                amended_cases, duplicates, status, error)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (started, datetime.now(), duration, total, new, amended, duplicates, status, error))
