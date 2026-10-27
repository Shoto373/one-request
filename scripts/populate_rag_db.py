"""Script to populate the PostgreSQL RAG travel inventory for 6 months.

Runs data population across:
- Ostrovok hotels across all destinations with full slugs, descriptions, amenities
- Tutu flights across routes for 180 days (Nov 2026 - May 2027)
- Tutu trains across routes for 180 days
- Ostrovok transfers across regional routes
- Direct booking links and provenance source URLs
"""
import logging
import sys
import os
from datetime import date

# Add workspace to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.rag.populator import populate_rag_database
from app.rag.service import get_db_url

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("populate_rag_db")

if __name__ == "__main__":
    db_url = get_db_url()
    log.info("Populating travel inventory in %s...", db_url)
    start_dt = date(2026, 11, 1)
    try:
        counts = populate_rag_database(db_url=db_url, start_date=start_dt, days=180)
        log.info("Successfully populated RAG inventory: %s", counts)
    except Exception as e:
        log.exception("Error populating RAG database: %s", e)
        sys.exit(1)
