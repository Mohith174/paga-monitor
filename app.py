#!/usr/bin/env python3
"""
PAGA Lead Gen - Flask Web Application
Run with: python app.py
Access at: http://localhost:5000
"""

from flask import Flask, render_template, jsonify, request, send_file
from datetime import datetime, timedelta
import csv
import io
from database import Database

app = Flask(__name__)
db = Database()


@app.route('/')
def dashboard():
    """Main dashboard"""
    stats = db.get_dashboard_stats()
    return render_template('dashboard.html', stats=stats)


@app.route('/leads')
def leads():
    """Lead list view"""
    status = request.args.get('status', None)
    limit = int(request.args.get('limit', 50))
    
    cases = db.get_recent_cases(limit=limit, status=status)
    return render_template('leads.html', cases=cases, current_status=status)


@app.route('/api/stats')
def api_stats():
    """API endpoint for dashboard stats"""
    stats = db.get_dashboard_stats()
    trends = db.get_filing_trends(days=30)
    cities = db.get_top_cities(limit=10)
    industries = db.get_industry_breakdown()
    employers = db.get_top_employers(min_cases=2, limit=10)
    
    return jsonify({
        'kpis': stats,
        'charts': {
            'trends': trends,
            'cities': cities,
            'industries': industries,
            'employers': employers
        }
    })


@app.route('/api/leads/<int:case_id>')
def api_lead_detail(case_id):
    """Get single lead details"""
    with db.get_conn() as conn:
        case = conn.execute("SELECT * FROM cases WHERE id = ?", (case_id,)).fetchone()
        if not case:
            return jsonify({'error': 'Not found'}), 404
        
        # Get notes
        notes = conn.execute("""
            SELECT * FROM notes WHERE case_id = ? ORDER BY created_at DESC
        """, (case_id,)).fetchall()
        
        # Get activity
        activity = conn.execute("""
            SELECT * FROM activity_log WHERE case_id = ? ORDER BY timestamp DESC
        """, (case_id,)).fetchall()
        
        return jsonify({
            'case': dict(case),
            'notes': [dict(n) for n in notes],
            'activity': [dict(a) for a in activity]
        })


@app.route('/api/leads/<int:case_id>/status', methods=['POST'])
def api_update_status(case_id):
    """Update lead status"""
    data = request.get_json()
    status = data.get('status')
    user = data.get('user', 'web_user')
    
    if status not in ['new', 'contacted', 'qualified', 'closed']:
        return jsonify({'error': 'Invalid status'}), 400
    
    db.update_case_status(case_id, status, user)
    
    if status == 'contacted':
        with db.get_conn() as conn:
            conn.execute(
                "UPDATE cases SET contacted_date = ? WHERE id = ?",
                (datetime.now(), case_id)
            )
    
    return jsonify({'success': True})


@app.route('/api/leads/<int:case_id>/note', methods=['POST'])
def api_add_note(case_id):
    """Add note to lead"""
    data = request.get_json()
    note = data.get('note', '')
    user = data.get('user', 'web_user')
    
    if not note:
        return jsonify({'error': 'Note cannot be empty'}), 400
    
    db.add_note(case_id, note, user)
    return jsonify({'success': True})


@app.route('/priority')
def priority_leads():
    """High priority leads view"""
    # Get cases with priority >= 8 (High) and join analysis
    cases = db.get_priority_leads(limit=50)
    return render_template('priority.html', cases=cases)



@app.route('/export/csv')
def export_csv():
    """Export leads to CSV"""
    days = int(request.args.get('days', 30))
    start_date = datetime.now() - timedelta(days=days)
    
    cases = db.get_recent_cases(limit=10000)
    
    # Create CSV
    output = io.StringIO()
    writer = csv.writer(output)
    
    # Header
    writer.writerow([
        'LWDA Number', 'Employer', 'City', 'ZIP', 'Filed Date', 'Type',
        'Employees', 'Law Firm', 'Attorney', 'Plaintiff', 'Status', 'Priority',
        'PDF URL', 'First Seen', 'Last Seen'
    ])
    
    # Data
    for case in cases:
        writer.writerow([
            case['lwda_number'],
            case['employer_name'],
            case.get('employer_city', ''),
            case.get('employer_zip', ''),
            case.get('submission_date', ''),
            case.get('submission_type', ''),
            case.get('num_employees', ''),
            case.get('law_firm', ''),
            case.get('attorney_name', ''),
            case.get('plaintiff_name', ''),
            case['status'],
            case['priority'],
            case.get('pdf_url', ''),
            case['first_seen'],
            case['last_seen']
        ])
    
    # Send file
    output.seek(0)
    return send_file(
        io.BytesIO(output.getvalue().encode('utf-8')),
        mimetype='text/csv',
        as_attachment=True,
        download_name=f'paga_leads_{datetime.now().strftime("%Y%m%d")}.csv'
    )


@app.route('/analytics')
def analytics():
    """Advanced analytics page"""
    return render_template('analytics.html')


@app.template_filter('timeago')
def timeago_filter(dt_str):
    """Convert datetime to 'X mins ago' format"""
    if not dt_str:
        return ''
    
    try:
        dt = datetime.fromisoformat(dt_str.replace('Z', '+00:00'))
        now = datetime.now()
        diff = now - dt
        
        if diff.days > 0:
            return f"{diff.days} day{'s' if diff.days > 1 else ''} ago"
        elif diff.seconds > 3600:
            hours = diff.seconds // 3600
            return f"{hours} hour{'s' if hours > 1 else ''} ago"
        elif diff.seconds > 60:
            mins = diff.seconds // 60
            return f"{mins} min{'s' if mins > 1 else ''} ago"
        else:
            return "just now"
    except:
        return ''


if __name__ == '__main__':
    print("="*70)
    print("PAGA Lead Gen - Web Dashboard")
    print("="*70)
    print("\nStarting server...")
    print("Access dashboard at: http://localhost:5001")
    print("\nPress Ctrl+C to stop\n")
    
    app.run(debug=True, host='0.0.0.0', port=5001)
