from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime, timezone


class Product(BaseModel):
    name: str
    url: str
    price: Optional[str] = None
    old_price: Optional[str] = None
    discount: Optional[str] = None
    rating: Optional[float] = None
    reviews: Optional[int] = None
    brand: Optional[str] = None
    sku: Optional[str] = None
    sizes: list[str] = Field(default_factory=list)
    colors: list[str] = Field(default_factory=list)
    stock: Optional[str] = None
    image: Optional[str] = None
    description: Optional[str] = None
    category: Optional[str] = None
    breadcrumb: Optional[str] = None
    scraped_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
