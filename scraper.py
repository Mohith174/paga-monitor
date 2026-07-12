#!/usr/bin/env python3
"""
Enhanced PAGA scraper - extracts additional fields
"""

import hashlib
from datetime import datetime
from typing import List, Dict, Tuple, Optional
from playwright.sync_api import sync_playwright


class PAGAScraper:
    """Fast scraper using Visualforce API"""

    # Direct GETs to PAGASearchResults now return 401; the results page is
    # only reachable by submitting the ViewState-signed search form.
    SEARCH_URL = "https://cadir.my.salesforce-sites.com/PagaSearch"
    FORM = "j_id0\\:Template\\:j_id21"
    
    def __init__(self):
        self.playwright = None
        self.browser = None
        self.page = None
    
    def __enter__(self):
        self._init()
        return self
    
    def __exit__(self, *args):
        self._close()
    
    def _init(self):
        """Initialize browser"""
        self.playwright = sync_playwright().start()
        self.browser = self.playwright.chromium.launch(
            headless=True,
            args=['--disable-gpu', '--no-sandbox']
        )
        context = self.browser.new_context(viewport={'width': 1280, 'height': 720})
        self.page = context.new_page()
        self.page.set_default_timeout(15000)
    
    def _close(self):
        """Cleanup"""
        if self.browser:
            self.browser.close()
        if self.playwright:
            self.playwright.stop()
    
    def scrape(
        self, 
        start_date: str,  # YYYY-MM-DD
        end_date: str,    # YYYY-MM-DD
        sub_type: str = "PAGA Notice"
    ) -> Tuple[List[Dict], Optional[str]]:
        """
        Scrape cases for date range
        Returns: (cases, error)
        """
        try:
            # Reach the results page through the search form (required for auth)
            self.page.goto(self.SEARCH_URL, wait_until="networkidle", timeout=45000)
            self.page.fill(f"#{self.FORM}\\:xxxpb1\\:xxxpbs2\\:startDate", start_date)
            self.page.fill(f"#{self.FORM}\\:xxxpb1\\:xxxpbs2\\:endDate", end_date)
            if sub_type:
                self.page.select_option(f"#{self.FORM}\\:xxxpb1\\:j_id35\\:subType", label=sub_type)
            with self.page.expect_navigation(wait_until="domcontentloaded", timeout=60000):
                self.page.click(f"#{self.FORM}\\:searchbt")
            self.page.wait_for_function("typeof Visualforce !== 'undefined'", timeout=15000)

            # Call API
            api_call = f"""
            new Promise((resolve, reject) => {{
                Visualforce.remoting.Manager.invokeAction(
                    'PAGAResultsController.getAllCases',
                    '', '', '{start_date}', '{end_date}', '', '', '', '', '', '', '', '', '', '', '{sub_type}',
                    function(results, event) {{
                        if (event.status) {{
                            resolve(results);
                        }} else {{
                            reject(event.message);
                        }}
                    }},
                    {{escape: false, timeout: 30000}}
                );
            }});
            """
            
            results = self.page.evaluate(api_call)
            cases = self._parse_results(results)
            return cases, None
            
        except Exception as e:
            return [], str(e)
    
    def _parse_results(self, results: list) -> List[Dict]:
        """Parse API response into case dictionaries"""
        cases = []
        
        for r in results:
            try:
                # Extract submission details
                sub_type = ""
                sub_date = ""
                sub_name = ""
                
                # Normalize attachments to list
                raw_att = r.get('Attachments__r')
                attachments = []
                if raw_att:
                    if isinstance(raw_att, list):
                        attachments = raw_att
                    elif isinstance(raw_att, dict):
                        # Salesforce sometimes returns {0: {...}, 1: {...}}
                        attachments = [v for k, v in raw_att.items()]
                        
                # Extract submission details
                sub_type = ""
                sub_date = ""
                sub_name = ""
                
                if attachments:
                    first_att = attachments[0]
                    sub_type = first_att.get('Type__c', '')
                    created = first_att.get('CreatedDate', '')
                    if created:
                        if isinstance(created, (int, float)):
                            # Handle millisecond timestamp
                            dt = datetime.fromtimestamp(created / 1000)
                            sub_date = dt.strftime('%Y-%m-%d')
                        elif 'T' in str(created):
                            sub_date = str(created).split('T')[0]
                    sub_name = first_att.get('Name', '')

                # Prefer the case's own filing date over the attachment date
                filing = r.get('Notice_Filing_Date__c')
                if isinstance(filing, (int, float)):
                    sub_date = datetime.fromtimestamp(filing / 1000).strftime('%Y-%m-%d')

                employer = r.get('Employer__r') or {}
                attorney = r.get('Filer_Attorney_for_PAGA_Case__r') or {}

                # Build case dict (new field names first, pre-2026 names as fallback)
                case = {
                    'lwda_number': r.get('Case_Number__c', ''),
                    'submission_name': sub_name,
                    'submission_type': sub_type,
                    'submission_date': sub_date,
                    'employer_name': employer.get('Name') or r.get('Employer_Name__c', ''),
                    'employer_city': employer.get('ShippingCity') or r.get('Employer_City__c', ''),
                    'employer_zip': employer.get('ShippingPostalCode') or r.get('Employer_ZIP__c', ''),
                    'num_employees': r.get('PAGA_Impacted_Employees__c') or r.get('Number_of_Impacted_Employees__c'),
                    'law_firm': r.get('Law_Firm_for_PAGA_Case__c') or r.get('Law_Firm_Name__c', ''),
                    'attorney_name': attorney.get('Name') or r.get('Attorney_Filer__c', ''),
                    'plaintiff_name': r.get('Plaintiff_for_PAGA_Case_Text__c') or r.get('Plaintiff__c', ''),
                    'court_case_number': r.get('Court_Case_Number__c', ''),
                    'pdf_url': self._build_pdf_url(attachments),
                    'detail_url': f"https://cadir.my.salesforce-sites.com/PagaSearch/PAGACaseDetails?id={r.get('Id', '')}",
                }
                
                # Add content hash
                case['content_hash'] = self._generate_hash(case)
                
                # Only add if has lwda_number
                if case['lwda_number']:
                    cases.append(case)
                    
            except Exception:
                continue
        
        return cases
    
    def _build_pdf_url(self, attachments: List[Dict]) -> str:
        """Extract PDF URL from attachments"""
        if not attachments:
            return ""
        
        for att in attachments:
            # Try Document_Id__c first, then fall back to Id if type matches
            doc_id = att.get('Document_Id__c')
            if not doc_id and att.get('Id'):
                doc_id = att.get('Id')
                
            if doc_id:
                return f"https://cadir.my.salesforce-sites.com/servlet/servlet.FileDownload?file={doc_id}"
        
        return ""
    
    def _generate_hash(self, case: Dict) -> str:
        """Generate content hash for deduplication"""
        content = '|'.join([
            str(case.get('lwda_number', '')),
            str(case.get('employer_name', '')),
            str(case.get('submission_date', '')),
            str(case.get('submission_type', '')),
            str(case.get('employer_city', ''))
        ])
        return hashlib.sha256(content.encode()).hexdigest()


def quick_test():
    """Test scraper"""
    from datetime import timedelta
    import time
    
    print("Testing enhanced scraper...")
    
    end = datetime.now()
    start = end - timedelta(days=1)
    
    start_time = time.time()
    
    with PAGAScraper() as scraper:
        cases, error = scraper.scrape(
            start.strftime('%Y-%m-%d'),
            end.strftime('%Y-%m-%d')
        )
    
    elapsed = time.time() - start_time
    
    if error:
        print(f"Error: {error}")
    else:
        print(f"\nFound {len(cases)} cases in {elapsed:.2f}s")
        if cases:
            case = cases[0]
            print(f"\nSample case:")
            print(f"  LWDA: {case['lwda_number']}")
            print(f"  Employer: {case['employer_name']}")
            print(f"  City: {case['employer_city']}")
            print(f"  Filed: {case['submission_date']}")
            print(f"  Type: {case['submission_type']}")


if __name__ == "__main__":
    quick_test()
