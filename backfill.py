#!/usr/bin/env python3
import time
from datetime import datetime, timedelta
from dateutil.relativedelta import relativedelta
from scraper import PAGAScraper
from database import Database

def backfill(start_date=None):
    db = Database()
    # Default: full history from 01/01/2024
    start_date = start_date or datetime(2024, 1, 1)
    end_date = datetime.now()
    
    current = start_date
    
    print(f"==================================================")
    print(f"Starting BACKFILL from {start_date.date()} to {end_date.date()}")
    print(f"Target: PAGA Notices")
    print(f"==================================================")
    
    with PAGAScraper() as scraper:
        while current < end_date:
            # Chunk by Month for stability
            next_step = current + relativedelta(months=1)
            # Don't go past today
            chunk_end = min(next_step, end_date)
            
            # Format dates for API
            s_str = current.strftime('%Y-%m-%d')
            # For the API, if we ask for a range, it gets it. 
            # Note: The existing scraper treats start/end as inclusive usually. 
            # We must verify we don't double count boundaries or miss them.
            # Salesforce usually treats string dates as inclusive.
            # To be safe, we subtract one day from end of this chunk? 
            # No, if we do [Jan 1 - Feb 1] then [Feb 1 - Mar 1], we might dup Feb 1.
            # But duplicate handling in DB handles this.
            
            e_str = chunk_end.strftime('%Y-%m-%d')
            
            print(f"Processing: {s_str} -> {e_str}...")
            
            try:
                # Use "PAGA Notice" as confirmed valid type
                cases, error = scraper.scrape(s_str, e_str, "PAGA Notice")
                if error:
                    print(f"  ❌ Error: {error}")
                else:
                    count = len(cases)
                    print(f"  ⬇️  Downloaded: {count} cases")
                    
                    # Save details
                    new_cnt = 0
                    amd_cnt = 0
                    dup_cnt = 0
                    
                    for c in cases:
                        res, _ = db.process_case(c)
                        if res == 'new':
                            new_cnt += 1
                        elif res == 'amended':
                            amd_cnt += 1
                        else:
                            dup_cnt += 1
                        
                    print(f"  💾 Saved: {new_cnt} new, {amd_cnt} amended, {dup_cnt} dups")
                    
            except Exception as e:
                print(f"  ❌ Critical Exception: {e}")
                import traceback
                traceback.print_exc()
                
            # Move to next chunk
            current = chunk_end
            
            # Small sleep to be nice to their server
            time.sleep(1)

    print("\n✅ Backfill Complete")

if __name__ == "__main__":
    import sys
    start = datetime.strptime(sys.argv[1], '%Y-%m-%d') if len(sys.argv) > 1 else None
    backfill(start)
