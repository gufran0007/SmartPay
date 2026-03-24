from pydantic import BaseModel
from datetime import datetime

class Invoice(BaseModel):
    filename: str
    content_type: str
    upload_time: datetime
