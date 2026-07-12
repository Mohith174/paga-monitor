#!/usr/bin/env python3
"""
Background scraper - runs every 5 minutes during business hours
Run with: python scheduler.py
"""

import time
from datetime import datetime, timedelta
import schedule
import pytz
from scraper import PAGAScraper
from database import Database

# Configuration
CHECK_INTERVAL_MINUTES = 5
BUSINESS_HOURS = (8, 18)  # 8 AM - 6 PM PST
TIMEZONE = pytz.timezone('America/Los_Angeles')


class ScraperScheduler:
    """Automated scraper scheduler"""
    
    def __init__(self):
        self.db = Database()
        self.running = True
    
    def is_business_hours(self) -> bool:
        """Check if within business hours"""
        now = datetime.now(TIMEZONE)
        return (
            now.weekday() < 5 and  # Mon-Fri
            BUSINESS_HOURS[0] <= now.hour < BUSINESS_HOURS[1]
        )
    
    def run_scrape(self):
        """Run one scrape cycle"""
        if not self.is_business_hours():
            print(f"[{datetime.now(TIMEZONE).strftime('%H:%M:%S')}] Outside business hours, skipping")
            return
        
        start_time = time.time()
        timestamp = datetime.now(TIMEZONE).strftime('%Y-%m-%d %H:%M:%S')
        
        print(f"\n{'='*70}")
        print(f"[{timestamp}] Starting scrape...")
        print('='*70)
        
        # Calculate date range (last 5 minutes to now, plus today)
        end = datetime.now()
        start = end - timedelta(minutes=CHECK_INTERVAL_MINUTES)
        
        # Always search today to catch everything
        search_start = end.date()
        search_end = end.date()
        
        try:
            # Scrape
            print(f"Searching for cases on {search_start}...")
            with PAGAScraper() as scraper:
                cases, error = scraper.scrape(
                    str(search_start),
                    str(search_end),
                    "PAGA Notice"
                )
            
            if error:
                print(f"❌ Scrape failed: {error}")
                self.db.log_run(
                    datetime.now(), time.time() - start_time,
                    0, 0, 0, 0, 'error', error
                )
                return
            
            print(f"Found {len(cases)} total cases")
            
            # Process cases
            new_count = 0
            amended_count = 0
            duplicate_count = 0
            
            for case in cases:
                action, case_id = self.db.process_case(case)
                
                if action == 'new':
                    new_count += 1
                    print(f"  🆕 NEW: {case['lwda_number']} - {case['employer_name']}")
                elif action == 'amended':
                    amended_count += 1
                    print(f"  📝 AMENDED: {case['lwda_number']}")
                else:
                    duplicate_count += 1
            
            # Log run
            duration = time.time() - start_time
            self.db.log_run(
                datetime.now(), duration,
                len(cases), new_count, amended_count, duplicate_count
            )
            
            # Print summary
            print(f"\nSummary:")
            print(f"  Total: {len(cases)}")
            print(f"  New: {new_count}")
            print(f"  Amended: {amended_count}")
            print(f"  Duplicates: {duplicate_count}")
            print(f"  Duration: {duration:.2f}s")
            print('='*70)
            
        except Exception as e:
            print(f"❌ Error: {e}")
            import traceback
            traceback.print_exc()
            
            self.db.log_run(
                datetime.now(), time.time() - start_time,
                0, 0, 0, 0, 'error', str(e)
            )
    
    def start(self):
        """Start the scheduler"""
        print("="*70)
        print("PAGA SCRAPER SCHEDULER")
        print("="*70)
        print(f"\nConfiguration:")
        print(f"  Check interval: {CHECK_INTERVAL_MINUTES} minutes")
        print(f"  Business hours: {BUSINESS_HOURS[0]}:00 - {BUSINESS_HOURS[1]}:00 PST")
        print(f"  Timezone: {TIMEZONE}")
        print(f"\nScheduler starting...")
        print(f"Press Ctrl+C to stop\n")
        
        # Run immediate check
        print("Running startup check...")
        self.run_scrape()
        
        # Schedule regular checks
        schedule.every(CHECK_INTERVAL_MINUTES).minutes.do(self.run_scrape)
        
        # Main loop
        while self.running:
            schedule.run_pending()
            time.sleep(60)  # Check every minute
        
        print("\nScheduler stopped")


if __name__ == "__main__":
    try:
        scheduler = ScraperScheduler()
        scheduler.start()
    except KeyboardInterrupt:
        print("\n\nShutting down gracefully...")
