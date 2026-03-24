"""
State Management for Smart Pay
Singleton instances for shared services
"""
from app.services.csv_service import CSVService

# Single shared instance - do not instantiate CSVService anywhere else
csv_service = CSVService()


def reload_csv_data():
    """Reload all CSV data"""
    global csv_service
    csv_service = CSVService()
    return csv_service
