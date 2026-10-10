"""
Vyapar Hub - Production-Grade SaaS B2B2C PWA Backend API
=========================================================
A multi-tenant, user-isolated FastAPI backend for small Kirana, retail, and wholesale merchants.
Strict isolation: each shopkeeper manages their own inventory, khata, and real-time orders.
Each customer browses real registered shops, places orders to specific shopkeepers, and tracks their own dues.
"""

import os
import re
import json
import uuid
import time
import random
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import List, Optional, Dict, Any, Literal
import urllib.parse

from fastapi import FastAPI, Depends, HTTPException, Query, status
from fastapi.responses import FileResponse, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sqlalchemy import (
    create_engine,
    Column,
    Integer,
    BigInteger,
    String,
    Numeric,
    Date,
    DateTime,
    Boolean,
    ForeignKey,
    Text,
    desc,
    asc,
    func,
    text,
    or_
)
from sqlalchemy.orm import declarative_base, sessionmaker, Session, relationship

# ============================================================================
# 1. DATABASE CONFIGURATION (MySQL with Resilient SQLite Fallback)
# ============================================================================

DB_USER = os.getenv("DB_USER", "root")
DB_PASSWORD = os.getenv("DB_PASSWORD", "password")
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = os.getenv("DB_PORT", "3306")
DB_NAME = os.getenv("DB_NAME", "vyapar_hub")

MYSQL_DATABASE_URL = os.getenv(
    "DATABASE_URL",
    f"mysql+pymysql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}?charset=utf8mb4"
)

def create_db_engine():
    try:
        mysql_engine = create_engine(
            MYSQL_DATABASE_URL,
            pool_pre_ping=True,
            pool_recycle=3600,
            connect_args={"connect_timeout": 2}
        )
        with mysql_engine.connect():
            pass
        print(f"[DATABASE] Connected to MySQL database '{DB_NAME}' successfully.")
        return mysql_engine
    except Exception as e:
        print(f"[DATABASE NOTICE] Using local SQLite ('sqlite:///./vyapar_hub.db') for execution ({e}).")
        return create_engine("sqlite:///./vyapar_hub.db", connect_args={"check_same_thread": False})

engine = create_db_engine()
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ============================================================================
# 2. SQLALCHEMY DATABASE MODELS
# ============================================================================

class User(Base):
    """Users table for Shopkeepers (Merchants) and Customers."""
    __tablename__ = "users"

    user_id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    role = Column(String(50), nullable=False, default="customer")  # 'shopkeeper', 'customer'
    full_name = Column(String(150), nullable=False)
    phone_number = Column(String(15), unique=True, index=True, nullable=False)
    email = Column(String(150), unique=True, nullable=True)
    pin = Column(String(50), nullable=True)
    shop_name = Column(String(200), nullable=True)
    shop_category = Column(String(100), default=None, nullable=True)
    latitude = Column(Numeric(10, 6), nullable=True)
    longitude = Column(Numeric(10, 6), nullable=True)
    address = Column(Text, nullable=True)
    avatar_url = Column(Text, nullable=True)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    inventory_items = relationship("Inventory", back_populates="shopkeeper", cascade="all, delete-orphan")
    sales = relationship("SalesHistory", back_populates="shopkeeper", foreign_keys="SalesHistory.shopkeeper_id")


class Inventory(Base):
    """Inventory table with dynamic decay expiry and smart pricing attributes."""
    __tablename__ = "inventory"

    item_id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    shopkeeper_id = Column(Integer, ForeignKey("users.user_id"), nullable=False, index=True)
    item_name = Column(String(255), nullable=False, index=True)
    sku = Column(String(100), index=True, nullable=True)
    category = Column(String(100), nullable=False, index=True)
    stock_quantity = Column(Numeric(10, 2), default=0.00, nullable=False)
    low_stock_threshold = Column(Numeric(10, 2), default=5.00, nullable=False)
    unit = Column(String(50), default="pcs", nullable=False)  # pcs, kg, litre, packet
    purchase_price = Column(Numeric(10, 2), nullable=False)
    recommended_selling_price = Column(Numeric(10, 2), nullable=False)
    current_selling_price = Column(Numeric(10, 2), nullable=False)
    expiry_date = Column(Date, nullable=True, index=True)
    seasonal_category = Column(String(50), default="All-Season", nullable=False)
    is_daily_need = Column(Boolean, default=False, nullable=False)
    image_url = Column(Text, nullable=True)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    shopkeeper = relationship("User", back_populates="inventory_items")
    sale_items = relationship("SalesHistoryItem", back_populates="item")


class Khata(Base):
    """Customer credit master with billing reminders."""
    __tablename__ = "khata"

    khata_id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    shopkeeper_id = Column(Integer, ForeignKey("users.user_id"), nullable=False, index=True)
    customer_id = Column(Integer, ForeignKey("users.user_id"), nullable=True, index=True)
    customer_name = Column(String(150), nullable=True)
    customer_phone = Column(String(20), nullable=True)
    credit_limit = Column(Numeric(10, 2), default=5000.00, nullable=False)
    current_balance = Column(Numeric(10, 2), default=0.00, nullable=False)  # Positive = Udhar / Debt
    guarantor_name = Column(String(150), nullable=True)
    guarantor_phone = Column(String(20), nullable=True)
    billing_reminder_date = Column(Date, nullable=True, index=True)
    reminder_status = Column(String(50), default="PENDING", nullable=False)  # PENDING, SENT, PAID, OVERDUE
    last_reminder_sent_at = Column(DateTime, nullable=True)
    last_payment_date = Column(DateTime, nullable=True)
    is_blocked = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    shopkeeper = relationship("User", foreign_keys=[shopkeeper_id])
    customer = relationship("User", foreign_keys=[customer_id])
    transactions = relationship("KhataTransaction", back_populates="khata_account", cascade="all, delete-orphan")


class KhataTransaction(Base):
    """Passbook ledger for every credit and payment action."""
    __tablename__ = "khata_transactions"

    transaction_id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    khata_id = Column(Integer, ForeignKey("khata.khata_id"), nullable=False, index=True)
    transaction_type = Column(String(50), nullable=False)  # 'GIVE_CREDIT' or 'ACCEPT_PAYMENT'
    amount = Column(Numeric(10, 2), nullable=False)
    balance_before = Column(Numeric(10, 2), nullable=False)
    balance_after = Column(Numeric(10, 2), nullable=False)
    note = Column(String(255), nullable=True)
    bill_invoice_no = Column(String(100), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    khata_account = relationship("Khata", back_populates="transactions")


class SalesHistory(Base):
    """Daily sales transaction records."""
    __tablename__ = "sales_history"

    sale_id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    shopkeeper_id = Column(Integer, ForeignKey("users.user_id"), nullable=False, index=True)
    customer_id = Column(Integer, ForeignKey("users.user_id"), nullable=True, index=True)
    customer_name = Column(String(150), default="Walk-In Customer", nullable=True)
    items_summary = Column(Text, nullable=True)
    total_amount = Column(Numeric(10, 2), nullable=False)
    discount_amount = Column(Numeric(10, 2), default=0.00, nullable=False)
    final_amount = Column(Numeric(10, 2), nullable=False)
    payment_mode = Column(String(50), default="CASH", nullable=False)  # CASH, UPI, KHATA_CREDIT
    sale_date = Column(DateTime, default=datetime.utcnow, index=True)

    shopkeeper = relationship("User", foreign_keys=[shopkeeper_id])
    customer = relationship("User", foreign_keys=[customer_id])
    items = relationship("SalesHistoryItem", back_populates="sale", cascade="all, delete-orphan")


class SalesHistoryItem(Base):
    """Detailed line items for each transaction."""
    __tablename__ = "sales_history_items"

    sale_item_id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    sale_id = Column(Integer, ForeignKey("sales_history.sale_id"), nullable=False, index=True)
    item_id = Column(Integer, ForeignKey("inventory.item_id"), nullable=True, index=True)
    item_name = Column(String(255), nullable=True)
    quantity = Column(Numeric(10, 2), nullable=False)
    unit_price = Column(Numeric(10, 2), nullable=False)
    purchase_price = Column(Numeric(10, 2), default=0.00, nullable=False)
    subtotal = Column(Numeric(10, 2), nullable=False)
    is_flash_sale_item = Column(Boolean, default=False, nullable=False)
    is_daily_need_item = Column(Boolean, default=False, nullable=False)

    sale = relationship("SalesHistory", back_populates="items")
    item = relationship("Inventory", back_populates="sale_items")


class Order(Base):
    """Customer orders routed directly to a specific shopkeeper."""
    __tablename__ = "orders"

    order_id = Column(String(50), primary_key=True, index=True)
    shopkeeper_id = Column(Integer, ForeignKey("users.user_id"), nullable=False, index=True)
    customer_id = Column(Integer, ForeignKey("users.user_id"), nullable=True, index=True)
    customer_name = Column(String(150), nullable=False)
    customer_phone = Column(String(20), nullable=False)
    delivery_address = Column(Text, nullable=True)
    total_amount = Column(Numeric(10, 2), nullable=False)
    status = Column(String(50), default="RECEIVED", nullable=False)  # RECEIVED, PACKED, DISPATCHED, DELIVERED
    payment_mode = Column(String(50), default="UPI", nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    items = relationship("OrderItem", back_populates="order", cascade="all, delete-orphan")
    shopkeeper = relationship("User", foreign_keys=[shopkeeper_id])
    customer = relationship("User", foreign_keys=[customer_id])


class OrderItem(Base):
    """Individual line items in an order."""
    __tablename__ = "order_items"

    order_item_id = Column(Integer, primary_key=True, autoincrement=True)
    order_id = Column(String(50), ForeignKey("orders.order_id"), nullable=False, index=True)
    item_id = Column(Integer, nullable=True)
    name = Column(String(200), nullable=False)
    price = Column(Numeric(10, 2), nullable=False)
    qty = Column(Integer, default=1, nullable=False)
    subtotal = Column(Numeric(10, 2), nullable=False)

    order = relationship("Order", back_populates="items")

class Message(Base):
    """Direct chat inquiries between customer and shopkeeper, plus automated system receipts/alerts."""
    __tablename__ = "messages"

    message_id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    sender_id = Column(Integer, ForeignKey("users.user_id"), nullable=False, index=True)
    receiver_id = Column(Integer, ForeignKey("users.user_id"), nullable=False, index=True)
    shopkeeper_id = Column(Integer, ForeignKey("users.user_id"), nullable=False, index=True)
    customer_id = Column(Integer, ForeignKey("users.user_id"), nullable=False, index=True)
    sender_role = Column(String(50), nullable=False)  # 'customer', 'shopkeeper'
    content = Column(Text, nullable=False)
    message_type = Column(String(50), default="TEXT", nullable=False)  # TEXT, RECEIPT_ALERT, ORDER_INQUIRY, PAYMENT_ALERT
    is_read = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    sender = relationship("User", foreign_keys=[sender_id])
    receiver = relationship("User", foreign_keys=[receiver_id])


class Receipt(Base):
    """Automated digital receipts generated via POS voice commands or payment settlement."""
    __tablename__ = "receipts"

    receipt_id = Column(String(50), primary_key=True, index=True)
    shopkeeper_id = Column(Integer, ForeignKey("users.user_id"), nullable=False, index=True)
    customer_id = Column(Integer, ForeignKey("users.user_id"), nullable=True, index=True)
    customer_name = Column(String(150), nullable=False)
    customer_phone = Column(String(20), nullable=True)
    items_summary = Column(Text, nullable=False)  # JSON-encoded array of items
    subtotal = Column(Numeric(10, 2), nullable=False)
    discount = Column(Numeric(10, 2), default=0.00, nullable=False)
    total_amount = Column(Numeric(10, 2), nullable=False)
    payment_status = Column(String(50), default="ADDED_TO_KHATA", nullable=False)  # ADDED_TO_KHATA, PAID_CASH, PAID_UPI
    source = Column(String(50), default="VOICE_COMMAND_POS", nullable=False)  # VOICE_COMMAND_POS, BARCODE_SCAN, MANUAL_POS, CUSTOMER_UPI_PAYMENT
    voice_transcript = Column(Text, nullable=True)
    is_viewed = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    shopkeeper = relationship("User", foreign_keys=[shopkeeper_id])
    customer = relationship("User", foreign_keys=[customer_id])



# Auto-create tables and execute non-destructive schema migrations
try:
    Base.metadata.create_all(bind=engine)
    with engine.connect() as conn:
        backend_name = engine.url.get_backend_name()
        if backend_name == "sqlite":
            cursor = conn.execute(text("PRAGMA table_info(users)"))
            user_cols = [row[1] for row in cursor.fetchall()]
            if "pin" not in user_cols:
                conn.execute(text("ALTER TABLE users ADD COLUMN pin VARCHAR(50)"))
            if "address" not in user_cols:
                conn.execute(text("ALTER TABLE users ADD COLUMN address TEXT"))
            if "avatar_url" not in user_cols:
                conn.execute(text("ALTER TABLE users ADD COLUMN avatar_url TEXT"))
            if "shop_category" not in user_cols:
                conn.execute(text("ALTER TABLE users ADD COLUMN shop_category VARCHAR(100) DEFAULT 'General Kirana'"))
            if "latitude" not in user_cols:
                conn.execute(text("ALTER TABLE users ADD COLUMN latitude NUMERIC(10, 6)"))
            if "longitude" not in user_cols:
                conn.execute(text("ALTER TABLE users ADD COLUMN longitude NUMERIC(10, 6)"))

            inv_cursor = conn.execute(text("PRAGMA table_info(inventory)"))
            inv_cols = [row[1] for row in inv_cursor.fetchall()]
            if "image_url" not in inv_cols:
                conn.execute(text("ALTER TABLE inventory ADD COLUMN image_url TEXT"))
            
            k_cursor = conn.execute(text("PRAGMA table_info(khata)"))
            khata_cols = [row[1] for row in k_cursor.fetchall()]
            if "customer_name" not in khata_cols:
                conn.execute(text("ALTER TABLE khata ADD COLUMN customer_name VARCHAR(150)"))
            if "customer_phone" not in khata_cols:
                conn.execute(text("ALTER TABLE khata ADD COLUMN customer_phone VARCHAR(20)"))
            if "credit_limit" not in khata_cols:
                conn.execute(text("ALTER TABLE khata ADD COLUMN credit_limit NUMERIC(10, 2) DEFAULT 5000.00"))
            if "guarantor_name" not in khata_cols:
                conn.execute(text("ALTER TABLE khata ADD COLUMN guarantor_name VARCHAR(150)"))
            if "guarantor_phone" not in khata_cols:
                conn.execute(text("ALTER TABLE khata ADD COLUMN guarantor_phone VARCHAR(20)"))

            order_items_cursor = conn.execute(text("PRAGMA table_info(order_items)"))
            order_items_cols = [row[1] for row in order_items_cursor.fetchall()]
            if "item_id" not in order_items_cols:
                conn.execute(text("ALTER TABLE order_items ADD COLUMN item_id INTEGER"))

            sh_cursor = conn.execute(text("PRAGMA table_info(sales_history)"))
            sh_cols = [row[1] for row in sh_cursor.fetchall()]
            if "customer_name" not in sh_cols:
                conn.execute(text("ALTER TABLE sales_history ADD COLUMN customer_name VARCHAR(150) DEFAULT 'Walk-In Customer'"))
            if "items_summary" not in sh_cols:
                conn.execute(text("ALTER TABLE sales_history ADD COLUMN items_summary TEXT"))

            shi_cursor = conn.execute(text("PRAGMA table_info(sales_history_items)"))
            shi_rows = shi_cursor.fetchall()
            shi_cols = [row[1] for row in shi_rows]
            if "item_name" not in shi_cols:
                conn.execute(text("ALTER TABLE sales_history_items ADD COLUMN item_name VARCHAR(255)"))

            item_id_row = next((r for r in shi_rows if r[1] == "item_id"), None)
            if item_id_row and item_id_row[3] == 1:
                conn.execute(text("PRAGMA foreign_keys=off"))
                conn.execute(text("""
                    CREATE TABLE IF NOT EXISTS sales_history_items_new (
                        sale_item_id INTEGER PRIMARY KEY AUTOINCREMENT,
                        sale_id INTEGER NOT NULL,
                        item_id INTEGER,
                        item_name VARCHAR(255),
                        quantity NUMERIC(10, 2) NOT NULL,
                        unit_price NUMERIC(10, 2) NOT NULL,
                        purchase_price NUMERIC(10, 2) NOT NULL DEFAULT 0.0,
                        subtotal NUMERIC(10, 2) NOT NULL,
                        is_flash_sale_item BOOLEAN NOT NULL DEFAULT 0,
                        is_daily_need_item BOOLEAN NOT NULL DEFAULT 0,
                        FOREIGN KEY(sale_id) REFERENCES sales_history (sale_id),
                        FOREIGN KEY(item_id) REFERENCES inventory (item_id)
                    )
                """))
                conn.execute(text("""
                    INSERT INTO sales_history_items_new (sale_item_id, sale_id, item_id, item_name, quantity, unit_price, purchase_price, subtotal, is_flash_sale_item, is_daily_need_item)
                    SELECT sale_item_id, sale_id, item_id, item_name, quantity, unit_price, purchase_price, subtotal, is_flash_sale_item, is_daily_need_item FROM sales_history_items
                """))
                conn.execute(text("DROP TABLE sales_history_items"))
                conn.execute(text("ALTER TABLE sales_history_items_new RENAME TO sales_history_items"))
                conn.execute(text("PRAGMA foreign_keys=on"))

            conn.commit()
    print("[DATABASE] Schema verification and migrations completed successfully.")
except Exception as ex:
    print(f"[WARNING] Table initialization notice: {ex}")


# ============================================================================
# 3. PYDANTIC SCHEMAS
# ============================================================================

class SignupRequest(BaseModel):
    full_name: str
    phone_number: str
    role: str = "customer"  # 'shopkeeper' or 'customer'
    shop_name: Optional[str] = None
    shop_category: Optional[str] = None
    address: Optional[str] = None
    email: Optional[str] = None
    pin: Optional[str] = "1234"
    avatar_url: Optional[str] = None

class LoginRequest(BaseModel):
    phone_number: str
    pin: Optional[str] = None
    role: Optional[str] = None
    shop_name: Optional[str] = None
    shop_category: Optional[str] = None

class ProfileUpdateRequest(BaseModel):
    full_name: Optional[str] = None
    phone_number: Optional[str] = None
    shop_name: Optional[str] = None
    shop_category: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    address: Optional[str] = None
    avatar_url: Optional[str] = None

class CreateInventoryItemRequest(BaseModel):
    shopkeeper_id: int
    item_name: str
    category: str
    stock_quantity: float = 0.0
    low_stock_threshold: float = 5.0
    unit: str = "pcs"
    purchase_price: float
    selling_price: float
    expiry_date: Optional[str] = None  # YYYY-MM-DD
    seasonal_category: str = "All-Season"
    is_daily_need: bool = False
    sku: Optional[str] = None
    image_url: Optional[str] = None

class UpdateInventoryItemRequest(BaseModel):
    shopkeeper_id: int
    item_name: Optional[str] = None
    category: Optional[str] = None
    stock_quantity: Optional[float] = None
    low_stock_threshold: Optional[float] = None
    unit: Optional[str] = None
    purchase_price: Optional[float] = None
    selling_price: Optional[float] = None
    expiry_date: Optional[str] = None
    seasonal_category: Optional[str] = None
    is_daily_need: Optional[bool] = None
    sku: Optional[str] = None
    image_url: Optional[str] = None

class CreateKhataAccountRequest(BaseModel):
    shopkeeper_id: int
    customer_name: str
    customer_phone: Optional[str] = None
    credit_limit: float = 5000.0
    initial_balance: float = 0.0
    billing_reminder_date: Optional[str] = None
    guarantor_name: Optional[str] = None
    guarantor_phone: Optional[str] = None

class UpdateGuarantorRequest(BaseModel):
    shopkeeper_id: int
    guarantor_name: Optional[str] = None
    guarantor_phone: Optional[str] = None

class KhataTransactionRequest(BaseModel):
    shopkeeper_id: int
    transaction_type: Literal["GIVE_CREDIT", "ACCEPT_PAYMENT"]
    amount: float
    note: Optional[str] = None
    override_limit: bool = False

class UpdateCreditLimitRequest(BaseModel):
    shopkeeper_id: int
    credit_limit: float

class OrderItemSchema(BaseModel):
    item_id: Optional[int] = None
    name: str
    price: float
    qty: int = 1
    shop: Optional[str] = None

class PlaceOrderRequest(BaseModel):
    customer_id: Optional[int] = None
    customer_name: str
    customer_phone: str
    shopkeeper_id: Optional[int] = None
    shop_id: Optional[int] = None
    items: List[OrderItemSchema]
    total_amount: float
    delivery_address: Optional[str] = None
    payment_mode: str = "UPI"

class OrderStatusUpdateRequest(BaseModel):
    status: str

class VoiceCommandEntryRequest(BaseModel):
    shopkeeper_id: Optional[int] = None
    raw_text: Optional[str] = None
    text: Optional[str] = None
    auto_execute: bool = True
    override_limit: bool = False

VoiceProcessRequest = VoiceCommandEntryRequest

class BarcodeScanRequest(BaseModel):
    shopkeeper_id: int
    barcode: str
    action: Literal["LOOKUP", "STOCK_IN", "QUICK_SALE"] = "LOOKUP"
    quantity: float = 1.0
    unit_price: Optional[float] = None
    customer_id: Optional[int] = None

class SmartPricingRequest(BaseModel):
    shopkeeper_id: int
    item_id: Optional[int] = None
    category: Optional[str] = None
    wholesale_inflation_rate_pct: float = 0.0
    target_margin_pct: float = 18.0
    apply_to_inventory: bool = False


class SendMessageRequest(BaseModel):
    sender_id: int
    receiver_id: int
    content: str
    message_type: Optional[str] = "TEXT"


class PayDuesRequest(BaseModel):
    customer_id: int
    khata_id: int
    amount: float
    payment_method: Optional[str] = "UPI"
    notes: Optional[str] = None

class BillingReminderTriggerRequest(BaseModel):
    shopkeeper_id: int
    khata_ids: Optional[List[int]] = None

class QuickCashSaleRequest(BaseModel):
    shopkeeper_id: int
    items: List[Dict[str, Any]] = []
    total_amount: float
    discount_amount: float = 0.0
    payment_mode: str = "CASH"  # "CASH" or "UPI"
    is_guest: bool = True
    customer_name: Optional[str] = "Walk-In Customer"
    notes: Optional[str] = "Walk-in cash counter sale"



# ============================================================================
# 4. FASTAPI APP INITIALIZATION
# ============================================================================

app = FastAPI(
    title="Vyapar Hub Backend",
    description="Next-gen B2B2C PWA SaaS Backend for Retail Kirana Shops.",
    version="2.1.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/", tags=["PWA Frontend"])
async def serve_frontend():
    """Serves the Vyapar Hub PWA index.html."""
    if os.path.exists("index.html"):
        return FileResponse("index.html")
    base_index = os.path.join(os.path.dirname(os.path.abspath(__file__)), "index.html")
    if os.path.exists(base_index):
        return FileResponse(base_index)
    return {"message": "Vyapar Hub Backend is running!"}


@app.get("/api/health", tags=["General"])
def health_check():
    """API Health status."""
    return {
        "app": "Vyapar Hub SaaS Backend",
        "status": "online",
        "database": engine.url.get_backend_name(),
        "server_time": datetime.utcnow().isoformat()
    }


# ============================================================================
# 5. AUTHENTICATION & PROFILE MANAGEMENT
# ============================================================================

@app.post("/api/auth/signup", tags=["Authentication"])
def auth_signup(payload: SignupRequest, db: Session = Depends(get_db)):
    clean_phone = payload.phone_number.strip().replace(" ", "").replace("+91", "")
    role = (payload.role or "customer").strip().lower()
    if role not in ["shopkeeper", "customer"]:
        role = "customer"

    # Validation: shop_name is NOT mandatory for 'customer'.
    # Only required or stored if role is 'shopkeeper'.
    if role == "shopkeeper":
        shop_title = (payload.shop_name or "").strip()
        if not shop_title:
            raise HTTPException(status_code=400, detail="Shop name is required for shopkeeper registration.")
        shop_category = (payload.shop_category or "Kirana & General Store").strip()
    else:
        shop_title = None  # Never store shop_name for customer
        shop_category = None

    existing = db.query(User).filter(User.phone_number == clean_phone).first()

    if existing:
        existing.full_name = payload.full_name.strip()
        existing.role = role
        if payload.pin:
            existing.pin = payload.pin.strip()
        existing.shop_name = shop_title
        if role == "shopkeeper" and shop_category:
            existing.shop_category = shop_category
        elif role == "customer":
            existing.shop_category = None
        if payload.address:
            existing.address = payload.address.strip()
        if payload.avatar_url:
            existing.avatar_url = payload.avatar_url
        if payload.email:
            existing.email = payload.email.strip()
        db.commit()
        db.refresh(existing)
        return {
            "status": "SUCCESS",
            "message": "User profile updated successfully.",
            "user": {
                "user_id": existing.user_id,
                "role": existing.role,
                "full_name": existing.full_name,
                "phone_number": existing.phone_number,
                "shop_name": existing.shop_name,
                "shop_category": existing.shop_category if existing.role == "shopkeeper" else None,
                "address": existing.address or ("Civil Lines, Mandsaur" if role == "customer" else "Mandsaur, Madhya Pradesh"),
                "avatar_url": existing.avatar_url,
                "pin": existing.pin
            }
        }

    # Brand new user with true empty state
    new_user = User(
        full_name=payload.full_name.strip(),
        phone_number=clean_phone,
        email=payload.email.strip() if payload.email else f"{clean_phone}@vyaparhub.local",
        role=role,
        pin=payload.pin.strip() if payload.pin else "1234",
        shop_name=shop_title,
        shop_category=shop_category if role == "shopkeeper" else None,
        address=payload.address.strip() if payload.address else ("Mandsaur Town, Madhya Pradesh" if role == "shopkeeper" else "Civil Lines, Mandsaur"),
        avatar_url=payload.avatar_url,
        is_active=True
    )
    db.add(new_user)
    db.commit()
    db.refresh(new_user)

    return {
        "status": "SUCCESS",
        "message": "User registered successfully.",
        "user": {
            "user_id": new_user.user_id,
            "role": new_user.role,
            "full_name": new_user.full_name,
            "phone_number": new_user.phone_number,
            "shop_name": new_user.shop_name,
            "shop_category": new_user.shop_category if new_user.role == "shopkeeper" else None,
            "address": new_user.address,
            "avatar_url": new_user.avatar_url,
            "pin": new_user.pin
        }
    }


@app.post("/api/auth/login", tags=["Authentication"])
@app.post("/api/auth/signin", tags=["Authentication"])
def auth_login(payload: LoginRequest, db: Session = Depends(get_db)):
    clean_phone = payload.phone_number.strip().replace(" ", "").replace("+91", "")
    requested_role = (payload.role or "").strip().lower() if payload.role else None

    # Validation: shop_name is NOT mandatory for 'customer'.
    # Only stored if role is 'shopkeeper'.
    if requested_role == "shopkeeper":
        shop_title = (payload.shop_name or "").strip() if payload.shop_name else None
    else:
        shop_title = None

    user = db.query(User).filter(User.phone_number == clean_phone).first()

    if not user:
        # Create fresh user for this phone
        assigned_role = requested_role or ("shopkeeper" if "98111" in clean_phone else "customer")
        if assigned_role == "shopkeeper":
            name = "Ramesh Gupta"
            shop = shop_title or "Ramesh Kirana Stores"
            addr = "Shop No. 12, Main Mandi Road, Mandsaur"
            cat = payload.shop_category or "Kirana & General Store"
        else:
            name = "Suresh Patel"
            shop = None
            addr = "House No. 42, Civil Lines, Mandsaur"
            cat = None

        user = User(
            full_name=name,
            phone_number=clean_phone,
            email=f"{clean_phone}@vyaparhub.local",
            role=assigned_role,
            shop_name=shop,
            shop_category=cat,
            address=addr,
            pin=payload.pin or "1234",
            is_active=True
        )
        db.add(user)
        db.commit()
        db.refresh(user)
    else:
        # If user explicitly logged in choosing a specific role, ensure role matches
        if requested_role:
            user.role = requested_role
            if requested_role == "customer":
                user.shop_name = None  # Customer must not have shop name
                user.shop_category = None
            elif requested_role == "shopkeeper":
                if shop_title:
                    user.shop_name = shop_title
                elif not user.shop_name:
                    user.shop_name = "My Kirana Store"
                if payload.shop_category:
                    user.shop_category = payload.shop_category.strip()
            db.commit()
            db.refresh(user)

    return {
        "status": "SUCCESS",
        "message": "Login authenticated.",
        "user": {
            "user_id": user.user_id,
            "role": user.role,
            "full_name": user.full_name,
            "phone_number": user.phone_number,
            "shop_name": user.shop_name,
            "shop_category": user.shop_category if user.role == "shopkeeper" else None,
            "address": user.address or ("Civil Lines, Mandsaur" if user.role == "customer" else "Mandsaur, Madhya Pradesh"),
            "avatar_url": user.avatar_url,
            "pin": user.pin
        }
    }


@app.get("/api/profile/{user_id}", tags=["Authentication"])
def get_user_profile(user_id: int, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.user_id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found.")
    return {
        "user_id": user.user_id,
        "role": user.role,
        "full_name": user.full_name,
        "phone_number": user.phone_number,
        "email": user.email,
        "shop_name": user.shop_name,
        "shop_category": user.shop_category or "General Kirana",
        "latitude": float(user.latitude) if user.latitude is not None else None,
        "longitude": float(user.longitude) if user.longitude is not None else None,
        "address": user.address,
        "avatar_url": user.avatar_url,
        "created_at": user.created_at.isoformat() if user.created_at else None
    }


@app.put("/api/profile/{user_id}", tags=["Authentication"])
def update_user_profile(user_id: int, payload: ProfileUpdateRequest, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.user_id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found.")

    if payload.full_name is not None and payload.full_name.strip():
        user.full_name = payload.full_name.strip()
    if payload.phone_number is not None and payload.phone_number.strip():
        clean = payload.phone_number.strip().replace(" ", "").replace("+91", "")
        user.phone_number = clean
    if payload.shop_name is not None:
        user.shop_name = payload.shop_name.strip()
    if payload.shop_category is not None:
        user.shop_category = payload.shop_category.strip()
    if payload.latitude is not None:
        user.latitude = Decimal(str(payload.latitude))
    if payload.longitude is not None:
        user.longitude = Decimal(str(payload.longitude))
    if payload.address is not None:
        user.address = payload.address.strip()
    if payload.avatar_url is not None:
        user.avatar_url = payload.avatar_url

    db.commit()
    db.refresh(user)

    return {
        "status": "SUCCESS",
        "message": "Profile updated successfully.",
        "user": {
            "user_id": user.user_id,
            "role": user.role,
            "full_name": user.full_name,
            "phone_number": user.phone_number,
            "shop_name": user.shop_name,
            "shop_category": user.shop_category or "General Kirana",
            "latitude": float(user.latitude) if user.latitude is not None else None,
            "longitude": float(user.longitude) if user.longitude is not None else None,
            "address": user.address,
            "avatar_url": user.avatar_url
        }
    }


# ============================================================================
# 6. INVENTORY MANAGEMENT (STRICT USER ISOLATION)
# ============================================================================

@app.get("/api/inventory", tags=["Inventory"])
def get_shopkeeper_inventory(shopkeeper_id: int = Query(...), db: Session = Depends(get_db)):
    """Fetches inventory items strictly for this logged-in shopkeeper."""
    today = date.today()
    items = db.query(Inventory).filter(
        Inventory.shopkeeper_id == shopkeeper_id,
        Inventory.is_active == True
    ).order_by(Inventory.item_id.desc()).all()

    item_list = []
    for it in items:
        days_left = (it.expiry_date - today).days if it.expiry_date else None
        is_low = float(it.stock_quantity) <= float(it.low_stock_threshold)
        item_list.append({
            "item_id": it.item_id,
            "shopkeeper_id": it.shopkeeper_id,
            "item_name": it.item_name,
            "sku": it.sku,
            "category": it.category,
            "stock_quantity": float(it.stock_quantity),
            "low_stock_threshold": float(it.low_stock_threshold),
            "unit": it.unit,
            "purchase_price": float(it.purchase_price),
            "selling_price": float(it.current_selling_price),
            "expiry_date": it.expiry_date.isoformat() if it.expiry_date else None,
            "days_until_expiry": days_left,
            "is_low_stock": is_low,
            "is_daily_need": it.is_daily_need,
            "seasonal_category": it.seasonal_category,
            "image_url": it.image_url
        })

    return {
        "shopkeeper_id": shopkeeper_id,
        "total_items": len(item_list),
        "items": item_list
    }


@app.post("/api/inventory", tags=["Inventory"])
def create_inventory_item(payload: CreateInventoryItemRequest, db: Session = Depends(get_db)):
    """Inserts a new inventory item strictly under the logged-in shopkeeper's ID."""
    exp_d = None
    if payload.expiry_date:
        try:
            exp_d = datetime.strptime(payload.expiry_date, "%Y-%m-%d").date()
        except Exception:
            pass

    new_item = Inventory(
        shopkeeper_id=payload.shopkeeper_id,
        item_name=payload.item_name.strip(),
        sku=payload.sku.strip() if payload.sku else None,
        category=payload.category.strip(),
        stock_quantity=Decimal(str(payload.stock_quantity)),
        low_stock_threshold=Decimal(str(payload.low_stock_threshold)),
        unit=payload.unit.strip(),
        purchase_price=Decimal(str(payload.purchase_price)),
        recommended_selling_price=Decimal(str(payload.selling_price)),
        current_selling_price=Decimal(str(payload.selling_price)),
        expiry_date=exp_d,
        seasonal_category=payload.seasonal_category,
        is_daily_need=payload.is_daily_need,
        image_url=payload.image_url.strip() if payload.image_url else None,
        is_active=True
    )
    db.add(new_item)
    db.commit()
    db.refresh(new_item)

    return {
        "status": "SUCCESS",
        "message": f"'{new_item.item_name}' added to inventory.",
        "item": {
            "item_id": new_item.item_id,
            "shopkeeper_id": new_item.shopkeeper_id,
            "item_name": new_item.item_name,
            "category": new_item.category,
            "stock_quantity": float(new_item.stock_quantity),
            "unit": new_item.unit,
            "purchase_price": float(new_item.purchase_price),
            "selling_price": float(new_item.current_selling_price),
            "expiry_date": new_item.expiry_date.isoformat() if new_item.expiry_date else None,
            "image_url": new_item.image_url
        }
    }


@app.put("/api/inventory/{item_id}", tags=["Inventory"])
def update_inventory_item(item_id: int, payload: UpdateInventoryItemRequest, db: Session = Depends(get_db)):
    """Updates an inventory item strictly owned by the shopkeeper (Full CRUD)."""
    item = db.query(Inventory).filter(
        Inventory.item_id == item_id,
        Inventory.shopkeeper_id == payload.shopkeeper_id,
        Inventory.is_active == True
    ).first()
    if not item:
        raise HTTPException(status_code=404, detail="Item not found or unauthorized.")

    if payload.item_name is not None and payload.item_name.strip():
        item.item_name = payload.item_name.strip()
    if payload.category is not None and payload.category.strip():
        item.category = payload.category.strip()
    if payload.stock_quantity is not None:
        item.stock_quantity = Decimal(str(max(0.0, payload.stock_quantity)))
    if payload.low_stock_threshold is not None:
        item.low_stock_threshold = Decimal(str(max(0.0, payload.low_stock_threshold)))
    if payload.unit is not None and payload.unit.strip():
        item.unit = payload.unit.strip()
    if payload.purchase_price is not None:
        item.purchase_price = Decimal(str(max(0.0, payload.purchase_price)))
    if payload.selling_price is not None:
        item.current_selling_price = Decimal(str(max(0.0, payload.selling_price)))
        item.recommended_selling_price = Decimal(str(max(0.0, payload.selling_price)))
    if payload.expiry_date is not None:
        if payload.expiry_date.strip():
            try:
                item.expiry_date = datetime.strptime(payload.expiry_date.strip(), "%Y-%m-%d").date()
            except Exception:
                pass
        else:
            item.expiry_date = None
    if payload.seasonal_category is not None:
        item.seasonal_category = payload.seasonal_category
    if payload.is_daily_need is not None:
        item.is_daily_need = payload.is_daily_need
    if payload.sku is not None:
        item.sku = payload.sku.strip() if payload.sku.strip() else None
    if payload.image_url is not None:
        item.image_url = payload.image_url.strip() if payload.image_url.strip() else None

    db.commit()
    db.refresh(item)
    return {
        "status": "SUCCESS",
        "message": f"'{item.item_name}' updated successfully.",
        "item": {
            "item_id": item.item_id,
            "shopkeeper_id": item.shopkeeper_id,
            "item_name": item.item_name,
            "category": item.category,
            "stock_quantity": float(item.stock_quantity),
            "low_stock_threshold": float(item.low_stock_threshold),
            "unit": item.unit,
            "purchase_price": float(item.purchase_price),
            "selling_price": float(item.current_selling_price),
            "expiry_date": item.expiry_date.isoformat() if item.expiry_date else None,
            "image_url": item.image_url,
            "sku": item.sku
        }
    }


@app.delete("/api/inventory/{item_id}", tags=["Inventory"])
def delete_inventory_item(item_id: int, shopkeeper_id: int = Query(...), db: Session = Depends(get_db)):
    item = db.query(Inventory).filter(
        Inventory.item_id == item_id,
        Inventory.shopkeeper_id == shopkeeper_id
    ).first()
    if not item:
        raise HTTPException(status_code=404, detail="Item not found or unauthorized.")
    item.is_active = False
    db.commit()
    return {"status": "SUCCESS", "message": f"Item '{item.item_name}' removed from inventory."}


@app.get("/api/alerts/smart-inventory", tags=["Inventory"])
def get_smart_inventory_alerts(
    shopkeeper_id: int = Query(..., description="Shopkeeper user ID"),
    expiry_days_window: int = Query(15, ge=1, le=90),
    db: Session = Depends(get_db)
):
    """Computes low stock and dynamic decay alerts strictly for this shopkeeper's active stock."""
    today = date.today()

    # Low stock
    low_stock_query = db.query(Inventory).filter(
        Inventory.shopkeeper_id == shopkeeper_id,
        Inventory.is_active == True,
        Inventory.stock_quantity <= Inventory.low_stock_threshold
    ).all()

    low_stock_alerts = []
    for item in low_stock_query:
        stock_val = float(item.stock_quantity)
        thresh = float(item.low_stock_threshold)
        low_stock_alerts.append({
            "item_id": item.item_id,
            "item_name": item.item_name,
            "category": item.category,
            "current_stock": stock_val,
            "threshold": thresh,
            "unit": item.unit,
            "suggested_reorder_qty": max(20.0, thresh * 3.0)
        })

    # Perishables expiring
    expiring_items = db.query(Inventory).filter(
        Inventory.shopkeeper_id == shopkeeper_id,
        Inventory.is_active == True,
        Inventory.expiry_date != None,
        Inventory.stock_quantity > 0
    ).all()

    flash_sale_alerts = []
    for it in expiring_items:
        days_left = (it.expiry_date - today).days
        if days_left > expiry_days_window:
            continue

        if days_left <= 0:
            tier = "EXPIRED"
            discount_pct = 75.0
        elif days_left <= 2:
            tier = "CRITICAL_WINDOW"
            discount_pct = 60.0
        elif days_left <= 5:
            tier = "HIGH_URGENCY"
            discount_pct = 40.0
        else:
            tier = "MODERATE_URGENCY"
            discount_pct = 20.0

        orig_price = float(it.current_selling_price)
        cost_p = float(it.purchase_price)
        discounted_price = round(orig_price * (1.0 - (discount_pct / 100.0)), 2)
        floor_price = round(cost_p * 0.40, 2)
        flash_sale_price = max(discounted_price, floor_price) if tier != "EXPIRED" else discounted_price

        flash_sale_alerts.append({
            "item_id": it.item_id,
            "item_name": it.item_name,
            "category": it.category,
            "available_stock": float(it.stock_quantity),
            "unit": it.unit,
            "expiry_date": it.expiry_date.isoformat(),
            "days_until_expiry": days_left,
            "urgency_tier": tier,
            "original_selling_price": orig_price,
            "dynamic_decay_discount_pct": discount_pct,
            "recommended_flash_sale_price": flash_sale_price
        })

    flash_sale_alerts.sort(key=lambda x: x["days_until_expiry"])

    return {
        "shopkeeper_id": shopkeeper_id,
        "evaluation_date": today.isoformat(),
        "summary": {
            "low_stock_count": len(low_stock_alerts),
            "flash_sale_count": len(flash_sale_alerts)
        },
        "low_stock_alerts": low_stock_alerts,
        "flash_sale_decay_alerts": flash_sale_alerts
    }


# ============================================================================
# 7. POS REGISTER SUMMARY & DATA ENTRY (VOICE & BARCODE)
# ============================================================================

@app.get("/api/pos/summary", tags=["Data Entry"])
def get_pos_summary(shopkeeper_id: int = Query(...), db: Session = Depends(get_db)):
    """Calculates today's register metrics strictly for the logged-in shopkeeper."""
    today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)

    sales = db.query(SalesHistory).filter(
        SalesHistory.shopkeeper_id == shopkeeper_id,
        SalesHistory.sale_date >= today_start
    ).all()

    total_sales = sum(float(s.final_amount) for s in sales)
    bills_count = len(sales)
    cash_collected = sum(float(s.final_amount) for s in sales if s.payment_mode in ["CASH", "UPI"])

    khata_txs = db.query(KhataTransaction).join(Khata, KhataTransaction.khata_id == Khata.khata_id).filter(
        Khata.shopkeeper_id == shopkeeper_id,
        KhataTransaction.transaction_type == "GIVE_CREDIT",
        KhataTransaction.created_at >= today_start
    ).all()
    udhaar_given = sum(float(tx.amount) for tx in khata_txs)

    total_inventory_items = db.query(Inventory).filter(
        Inventory.shopkeeper_id == shopkeeper_id,
        Inventory.is_active == True
    ).count()

    active_orders_count = db.query(Order).filter(
        Order.shopkeeper_id == shopkeeper_id,
        Order.status.in_(["RECEIVED", "PENDING", "ACCEPTED", "PACKED", "OUT_FOR_DELIVERY"])
    ).count()

    khatas = db.query(Khata).filter(Khata.shopkeeper_id == shopkeeper_id).all()
    total_khata_due = sum(float(k.current_balance) for k in khatas)
    limit_breaches = sum(1 for k in khatas if float(k.current_balance) >= float(k.credit_limit or 2000.0))

    return {
        "shopkeeper_id": shopkeeper_id,
        "total_sales": total_sales,
        "bills_count": bills_count,
        "cash_collected": cash_collected,
        "udhaar_given": udhaar_given,
        "total_inventory_items": total_inventory_items,
        "active_orders_count": active_orders_count,
        "total_khata_due": total_khata_due,
        "credit_limit_breaches": limit_breaches,
        "total_customers_count": len(khatas)
    }


@app.post("/api/pos/quick-sale", tags=["Data Entry"])
def execute_quick_sale(payload: QuickCashSaleRequest, db: Session = Depends(get_db)):
    """
    Executes a quick cash sale without requiring customer name or phone number.
    Safeguards customer privacy (especially female customers and rapid cash buyers),
    deducts inventory when matched, and records strictly under cash sales.
    """
    shopkeeper = db.query(User).filter(User.user_id == payload.shopkeeper_id).first()
    if not shopkeeper:
        raise HTTPException(status_code=404, detail="Shopkeeper not found.")

    total_dec = Decimal(str(round(payload.total_amount, 2)))
    disc_dec = Decimal(str(round(payload.discount_amount, 2)))
    final_dec = max(Decimal("0.00"), total_dec - disc_dec)

    payment_mode = payload.payment_mode.upper() if payload.payment_mode else "CASH"
    if payment_mode not in ["CASH", "UPI"]:
        payment_mode = "CASH"

    parsed_items = []
    if payload.items:
        for it in payload.items:
            name = str(it.get("name", "Grocery Item")).strip()
            qty = float(it.get("qty", 1.0))
            unit = str(it.get("unit", "pcs"))

            # Deduct inventory if matched
            inv_match = None
            if it.get("item_id"):
                inv_match = db.query(Inventory).filter(
                    Inventory.shopkeeper_id == payload.shopkeeper_id,
                    Inventory.item_id == it["item_id"],
                    Inventory.is_active == True
                ).first()
            if not inv_match:
                inv_match = db.query(Inventory).filter(
                    Inventory.shopkeeper_id == payload.shopkeeper_id,
                    Inventory.is_active == True,
                    Inventory.item_name.ilike(name)
                ).order_by(Inventory.item_id.desc()).first()

            rate = float(it.get("rate", it.get("price", 0.0)))
            if rate <= 0 and inv_match:
                rate = float(inv_match.current_selling_price)
            if rate <= 0:
                for k, v in STANDARD_ITEMS.items():
                    if k in name.lower():
                        rate = float(v["rate"])
                        break
            if rate <= 0:
                rate = 40.0

            subtotal = float(it.get("subtotal", qty * rate))
            if subtotal <= 0:
                subtotal = qty * rate

            if inv_match:
                inv_match.stock_quantity = max(Decimal("0.00"), inv_match.stock_quantity - Decimal(str(qty)))

            parsed_items.append({
                "item_id": inv_match.item_id if inv_match else it.get("item_id"),
                "name": inv_match.item_name if inv_match else name,
                "qty": qty,
                "unit": unit,
                "rate": rate,
                "subtotal": subtotal
            })
    else:
        # Check if payload.notes has items or product names
        notes_str = (payload.notes or "").strip()
        matched_from_notes = []
        if notes_str and not any(k in notes_str.lower() for k in ["walk-in cash counter sale", "walk-in cash sale"]):
            db_inv = db.query(Inventory).filter(
                Inventory.shopkeeper_id == payload.shopkeeper_id,
                Inventory.is_active == True
            ).all()
            inv_by_name = {inv.item_name.lower(): inv for inv in db_inv}

            segments = re.split(r'[,+&]|\band\b', notes_str, flags=re.IGNORECASE)
            for seg in segments:
                seg_clean = seg.strip()
                m = re.match(r'(?:(\d+(?:\.\d+)?)\s*[xX]?\s*)?(.+)', seg_clean)
                if m:
                    q = float(m.group(1)) if m.group(1) else 1.0
                    n = m.group(2).strip()
                    n_lower = n.lower()

                    cand_inv = None
                    for inv_k, inv_v in inv_by_name.items():
                        if n_lower in inv_k or inv_k in n_lower:
                            cand_inv = inv_v
                            break
                    if not cand_inv:
                        for std_k, std_v in STANDARD_ITEMS.items():
                            if std_k in n_lower:
                                for inv_k, inv_v in inv_by_name.items():
                                    if std_k in inv_k or std_v["name"].lower() in inv_k:
                                        cand_inv = inv_v
                                        break
                                if not cand_inv:
                                    cand_name = std_v["name"]
                                    cand_rate = float(std_v["rate"])
                                    matched_from_notes.append({
                                        "item_id": None,
                                        "name": cand_name,
                                        "qty": q,
                                        "unit": std_v["unit"],
                                        "rate": cand_rate,
                                        "subtotal": q * cand_rate
                                    })
                                    break
                    if cand_inv:
                        cand_rate = float(cand_inv.current_selling_price)
                        cand_inv.stock_quantity = max(Decimal("0.00"), cand_inv.stock_quantity - Decimal(str(q)))
                        matched_from_notes.append({
                            "item_id": cand_inv.item_id,
                            "name": cand_inv.item_name,
                            "qty": q,
                            "unit": cand_inv.unit,
                            "rate": cand_rate,
                            "subtotal": q * cand_rate
                        })

        if matched_from_notes:
            parsed_items.extend(matched_from_notes)
        else:
            parsed_items.append({
                "item_id": None,
                "name": "General Grocery Item",
                "qty": 1.0,
                "unit": "sale",
                "rate": float(final_dec),
                "subtotal": float(final_dec)
            })

    cust_display = payload.customer_name.strip() if payload.customer_name else "Walk-In Customer"
    items_summary_str = ", ".join([f"{it['qty']}x {it['name']}" for it in parsed_items])

    # Record SalesHistory with customer_name and items_summary
    sale = SalesHistory(
        shopkeeper_id=payload.shopkeeper_id,
        customer_id=None,
        customer_name=cust_display,
        items_summary=items_summary_str,
        total_amount=total_dec,
        discount_amount=disc_dec,
        final_amount=final_dec,
        payment_mode=payment_mode,
        sale_date=datetime.utcnow()
    )
    db.add(sale)
    db.flush()

    # Add SalesHistoryItem records
    for it in parsed_items:
        inv_item = None
        if it.get("item_id"):
            inv_item = db.query(Inventory).filter(
                Inventory.shopkeeper_id == payload.shopkeeper_id,
                Inventory.item_id == it["item_id"]
            ).first()
        if not inv_item:
            inv_item = db.query(Inventory).filter(
                Inventory.shopkeeper_id == payload.shopkeeper_id,
                Inventory.item_name.ilike(it["name"])
            ).order_by(Inventory.item_id.desc()).first()

        shi = SalesHistoryItem(
            sale_id=sale.sale_id,
            item_id=inv_item.item_id if inv_item else None,
            item_name=it["name"],
            quantity=Decimal(str(it["qty"])),
            unit_price=Decimal(str(it["rate"])),
            purchase_price=inv_item.purchase_price if inv_item else Decimal("0.00"),
            subtotal=Decimal(str(it["subtotal"]))
        )
        db.add(shi)

    receipt_no = f"RCP-WALK-{int(time.time() * 1000) % 10000000}-{uuid.uuid4().hex[:4].upper()}"
    receipt = Receipt(
        receipt_id=receipt_no,
        shopkeeper_id=payload.shopkeeper_id,
        customer_id=None,
        customer_name=cust_display,
        customer_phone=None,
        items_summary=json.dumps(parsed_items),
        subtotal=total_dec,
        discount=disc_dec,
        total_amount=final_dec,
        payment_status="PAID_CASH" if payment_mode == "CASH" else "PAID_UPI",
        source="WALK_IN_CASH_POS",
        voice_transcript=payload.notes or "Walk-in cash counter sale",
        is_viewed=True
    )
    db.add(receipt)
    db.commit()

    return {
        "status": "SUCCESS",
        "sale_id": sale.sale_id,
        "receipt_id": receipt_no,
        "total_amount": float(final_dec),
        "payment_mode": payment_mode,
        "is_guest": True,
        "message": "Quick Walk-In cash sale completed successfully (Privacy Protected).",
        "receipt": {
            "receipt_id": receipt_no,
            "shop_name": shopkeeper.shop_name or f"{shopkeeper.full_name}'s Kirana",
            "customer_name": cust_display,
            "customer_phone": "N/A (Cash Guest)",
            "items": parsed_items,
            "subtotal": float(total_dec),
            "discount": float(disc_dec),
            "total_amount": float(final_dec),
            "payment_status": "PAID_CASH" if payment_mode == "CASH" else "PAID_UPI",
            "created_at": receipt.created_at.isoformat()
        }
    }


STANDARD_ITEMS = {
    "jeera": {"name": "Loose Jeera", "unit": "kg", "rate": 280.0, "category": "Loose Groceries & Spices"},
    "cumin": {"name": "Loose Jeera", "unit": "kg", "rate": 280.0, "category": "Loose Groceries & Spices"},
    "besan": {"name": "Chana Besan", "unit": "kg", "rate": 80.0, "category": "Staples & Grains"},
    "poha": {"name": "Thick Poha", "unit": "kg", "rate": 50.0, "category": "Staples & Grains"},
    "maida": {"name": "Refined Maida", "unit": "kg", "rate": 45.0, "category": "Staples & Grains"},
    "suji": {"name": "Suji (Rava)", "unit": "kg", "rate": 48.0, "category": "Staples & Grains"},
    "haldi": {"name": "Haldi Powder", "unit": "kg", "rate": 220.0, "category": "Loose Groceries & Spices"},
    "turmeric": {"name": "Haldi Powder", "unit": "kg", "rate": 220.0, "category": "Loose Groceries & Spices"},
    "mirch": {"name": "Lal Mirch Powder", "unit": "kg", "rate": 260.0, "category": "Loose Groceries & Spices"},
    "chilli": {"name": "Lal Mirch Powder", "unit": "kg", "rate": 260.0, "category": "Loose Groceries & Spices"},
    "dhaniya": {"name": "Dhaniya Powder", "unit": "kg", "rate": 180.0, "category": "Loose Groceries & Spices"},
    "coriander": {"name": "Dhaniya Powder", "unit": "kg", "rate": 180.0, "category": "Loose Groceries & Spices"},
    "maggi": {"name": "Maggi Noodles", "unit": "packet", "rate": 14.0, "category": "Snacks & Branded"},
    "noodles": {"name": "Maggi Noodles", "unit": "packet", "rate": 14.0, "category": "Snacks & Branded"},
    "sugar": {"name": "Sugar (Cheeni)", "unit": "kg", "rate": 45.0, "category": "Staples & Grains"},
    "cheeni": {"name": "Sugar (Cheeni)", "unit": "kg", "rate": 45.0, "category": "Staples & Grains"},
    "milk": {"name": "Fresh Milk (Doodh)", "unit": "packet", "rate": 32.0, "category": "Dairy & Fresh"},
    "doodh": {"name": "Fresh Milk (Doodh)", "unit": "packet", "rate": 32.0, "category": "Dairy & Fresh"},
    "bread": {"name": "Brown Bread", "unit": "packet", "rate": 30.0, "category": "Staples & Grains"},
    "atta": {"name": "Chakki Fresh Atta", "unit": "kg", "rate": 42.0, "category": "Staples & Grains"},
    "flour": {"name": "Wheat Flour", "unit": "kg", "rate": 40.0, "category": "Staples & Grains"},
    "oil": {"name": "Refined Mustard Oil", "unit": "litre", "rate": 145.0, "category": "Cooking Oils"},
    "tel": {"name": "Mustard Oil", "unit": "litre", "rate": 145.0, "category": "Cooking Oils"},
    "tea": {"name": "Premium Tea (Chai)", "unit": "packet", "rate": 90.0, "category": "General Kirana"},
    "chai": {"name": "Tea Patti", "unit": "packet", "rate": 90.0, "category": "General Kirana"},
    "rice": {"name": "Basmati Rice", "unit": "kg", "rate": 65.0, "category": "Staples & Grains"},
    "chawal": {"name": "Rice (Chawal)", "unit": "kg", "rate": 65.0, "category": "Staples & Grains"},
    "dal": {"name": "Toor Dal (Arhar)", "unit": "kg", "rate": 120.0, "category": "Staples & Grains"},
    "biscuit": {"name": "Glucose Biscuits", "unit": "pack", "rate": 20.0, "category": "Snacks & Branded"},
    "soap": {"name": "Bath Soap", "unit": "pcs", "rate": 35.0, "category": "General Kirana"},
    "butter": {"name": "Amul Butter", "unit": "pack", "rate": 56.0, "category": "Dairy & Fresh"},
    "paneer": {"name": "Fresh Paneer", "unit": "pack", "rate": 115.0, "category": "Dairy & Fresh"},
    "salt": {"name": "Tata Salt", "unit": "kg", "rate": 26.0, "category": "Staples & Grains"},
    "namak": {"name": "Tata Salt", "unit": "kg", "rate": 26.0, "category": "Staples & Grains"},
    "ghee": {"name": "Desi Ghee", "unit": "kg", "rate": 350.0, "category": "Dairy & Fresh"},
}


@app.get("/api/pos/quick-tap-grid", tags=["Data Entry"])
def get_quick_tap_grid(
    shopkeeper_id: int = Query(...),
    category: Optional[str] = Query(None),
    db: Session = Depends(get_db)
):
    """
    Returns dynamically curated inventory items for the shop's Quick-Tap Counter POS Grid.
    Items are ranked by sales frequency, recent addition (voice auto-cataloged items),
    and staple essentials for instant one-touch counter billing.
    """
    shopkeeper = db.query(User).filter(User.user_id == shopkeeper_id).first()
    if not shopkeeper:
        raise HTTPException(status_code=404, detail="Shopkeeper not found.")

    query = db.query(Inventory).filter(
        Inventory.shopkeeper_id == shopkeeper_id,
        Inventory.is_active == True
    )

    if category and category.lower() not in ["all", "all items", ""]:
        cat_lower = category.lower()
        if "fast" in cat_lower or "moving" in cat_lower or "popular" in cat_lower:
            pass  # Filtered after sorting
        elif "spices" in cat_lower or "loose" in cat_lower:
            query = query.filter(
                (Inventory.category.ilike("%Spices%")) |
                (Inventory.category.ilike("%Loose%")) |
                (Inventory.item_name.ilike("%Loose%")) |
                (Inventory.item_name.ilike("%Jeera%"))
            )
        elif "dairy" in cat_lower:
            query = query.filter(
                (Inventory.category.ilike("%Dairy%")) |
                (Inventory.item_name.ilike("%Milk%")) |
                (Inventory.item_name.ilike("%Doodh%")) |
                (Inventory.item_name.ilike("%Paneer%"))
            )
        elif "staple" in cat_lower or "grain" in cat_lower:
            query = query.filter(
                (Inventory.category.ilike("%Staple%")) |
                (Inventory.category.ilike("%Grain%")) |
                (Inventory.category.ilike("%Grocery%"))
            )
        else:
            query = query.filter(Inventory.category.ilike(f"%{category}%"))

    items = query.all()

    # Calculate sales counts per item for this shopkeeper
    sales_counts = dict(
        db.query(
            SalesHistoryItem.item_id,
            func.count(SalesHistoryItem.sale_item_id)
        ).join(SalesHistory, SalesHistoryItem.sale_id == SalesHistory.sale_id)
        .filter(SalesHistory.shopkeeper_id == shopkeeper_id)
        .group_by(SalesHistoryItem.item_id).all()
    )

    results = []
    now = datetime.utcnow()
    for it in items:
        sc = sales_counts.get(it.item_id, 0)
        is_voice_added = bool(it.sku and it.sku.startswith("VOICE-"))
        is_recent = is_voice_added or bool((now - it.created_at).total_seconds() < 86400)
        is_popular = (sc >= 1) or bool(it.is_daily_need)

        # Smart Emoji Icons
        name_lower = it.item_name.lower()
        if any(k in name_lower for k in ["jeera", "cumin", "masala", "mirch", "haldi", "spices", "clove"]):
            icon = "🌿"
        elif any(k in name_lower for k in ["milk", "doodh", "paneer", "butter", "ghee", "dahi"]):
            icon = "🥛"
        elif any(k in name_lower for k in ["sugar", "cheeni"]):
            icon = "🍬"
        elif any(k in name_lower for k in ["atta", "flour", "besan", "wheat", "maida", "suji", "rice", "chawal"]):
            icon = "🌾"
        elif any(k in name_lower for k in ["bread", "toast"]):
            icon = "🍞"
        elif any(k in name_lower for k in ["oil", "tel", "mustard"]):
            icon = "🫗"
        elif any(k in name_lower for k in ["biscuit", "cookie", "chips", "maggi", "namkeen"]):
            icon = "🍪"
        elif any(k in name_lower for k in ["tea", "chai", "coffee"]):
            icon = "☕"
        elif any(k in name_lower for k in ["soap", "shampoo", "paste", "detergent"]):
            icon = "🧼"
        else:
            icon = "🛒"

        badge = None
        if is_voice_added:
            badge = "✨ Voice Added"
        elif sc >= 2:
            badge = "⭐ Hot Seller"
        elif it.is_daily_need:
            badge = "Daily Need"

        results.append({
            "item_id": it.item_id,
            "item_name": it.item_name,
            "sku": it.sku,
            "category": it.category,
            "price": float(it.current_selling_price),
            "unit": it.unit,
            "stock_quantity": float(it.stock_quantity),
            "sales_count": sc,
            "is_popular": is_popular,
            "is_recent": is_recent,
            "is_voice_added": is_voice_added,
            "icon": icon,
            "badge": badge
        })

    # Filter by fast moving if requested
    if category and ("fast" in category.lower() or "popular" in category.lower()):
        results = [x for x in results if x["is_popular"] or x["is_recent"]]

    # Sort: Voice-added first, then sales count desc, then popular, then item_id desc
    results.sort(
        key=lambda x: (
            1 if x["is_voice_added"] else 0,
            x["sales_count"],
            1 if x["is_popular"] else 0,
            x["item_id"]
        ),
        reverse=True
    )

    categories_list = ["All", "⭐ Fast Moving", "🌿 Spices & Loose", "🌾 Staples & Grains", "🥛 Dairy & Fresh"]

    return {
        "status": "SUCCESS",
        "shopkeeper_id": shopkeeper_id,
        "total_items": len(results),
        "categories": categories_list,
        "items": results
    }



def handle_voice_stock_addition(raw_text: str, target_shop_id: int, db: Session):
    """
    Parses spoken inventory stock addition in Hindi/Hinglish/English:
    - Extracts item name, quantity, unit, price, and optional expiry
    - Updates existing inventory or creates new item with VOICE- SKU prefix
    - Reflects immediately in Inventory list and Quick-Tap Counter Grid
    """
    text_lower = raw_text.strip().lower()

    # 1. Spoken price extraction
    spoken_price = None
    price_match = re.search(r'(?:at|@|rate|price|ke|bhav|rupaye|rupees|rs\.?|₹|for|cost|each|per)\s*(\d+(?:\.\d+)?)\s*(?:rupees|rupaye|rs\.?|₹|\/kg|\/packet|\/pcs|\/piece)?', text_lower)
    if not price_match:
        price_match = re.search(r'(\d+(?:\.\d+)?)\s*(?:rupees|rupaye|rs\.?|₹|\/kg|\/packet|\/pcs|\/piece)', text_lower)
    if price_match:
        try:
            spoken_price = float(price_match.group(1))
        except Exception:
            spoken_price = None

    text_clean_price = text_lower
    if price_match:
        text_clean_price = text_lower[:price_match.start()] + " " + text_lower[price_match.end():]

    # 2. Expiry date extraction for dynamic decay flash sales (e.g. "expiry 3 din", "expiring in 2 days")
    expiry_days = None
    exp_match = re.search(r'(?:expiry|expiring|expire)\s*(?:in|me)?\s*(\d+)\s*(?:days?|din)', text_clean_price)
    if not exp_match:
        exp_match = re.search(r'(\d+)\s*(?:days?|din)\s*(?:expiry|me\s*expire)', text_clean_price)
    if exp_match:
        try:
            expiry_days = int(exp_match.group(1))
            text_clean_price = text_clean_price[:exp_match.start()] + " " + text_clean_price[exp_match.end():]
        except Exception:
            pass

    # 3. Quantity and Unit extraction
    qty_regex = re.search(
        r'(\d+(?:\.\d+)?)\s*(packets|packet|pkts|pkt|litres|litre|ltr|l|bottles|bottle|pieces|piece|pcs|packs|pack|kilos|kilo|kg|grams|gram|gms|gm|g|bori|bags|bag|pau|boxes|box|peti)?\b',
        text_clean_price
    )
    if qty_regex:
        qty = float(qty_regex.group(1))
        raw_unit = (qty_regex.group(2) or "").lower()
        if raw_unit in ["kilos", "kilo", "kg"]:
            unit = "kg"
        elif raw_unit in ["grams", "gram", "gms", "gm", "g"]:
            unit = "gram"
        elif raw_unit in ["litres", "litre", "ltr", "l"]:
            unit = "litre"
        elif raw_unit in ["packets", "packet", "pkts", "pkt", "packs", "pack"]:
            unit = "packet"
        elif raw_unit in ["pieces", "piece", "pcs", "bottles", "bottle"]:
            unit = "pcs"
        elif raw_unit in ["bori", "bags", "bag"]:
            unit = "bag"
        elif raw_unit in ["boxes", "box", "peti"]:
            unit = "box"
        else:
            unit = "kg" if "loose" in text_lower else "pcs"
        qty_span = qty_regex.span()
        text_no_qty = text_clean_price[:qty_span[0]] + " " + text_clean_price[qty_span[1]:]
    else:
        qty = 1.0
        unit = "kg" if "loose" in text_lower else "pcs"
        text_no_qty = text_clean_price

    # 4. Check for isolated number as price if not yet identified
    if spoken_price is None:
        isolated_num = re.search(r'\b(\d+(?:\.\d+)?)\b', text_no_qty)
        if isolated_num:
            try:
                spoken_price = float(isolated_num.group(1))
                text_no_qty = text_no_qty[:isolated_num.start()] + " " + text_no_qty[isolated_num.end():]
            except Exception:
                pass

    # 5. Stop words removal
    stop_words = [
        "laaya", "laya", "laaye", "laye", "added", "add", "stock in", "stock inward", "stock add", "stock", "add karo",
        "jod do", "jodo", "daalo", "nayi item", "item", "inward", "catalog me", "auto catalog",
        "naya maal", "naya stock", "naye maal",
        "at", "per", "me", "ka", "ki", "ke", "bhav", "rate", "rupees", "rupaye", "rs", "₹", "pe", "ko", "hai",
        "expiry", "expire", "din", "days"
    ]
    name_tokens = text_no_qty
    for sw in sorted(stop_words, key=len, reverse=True):
        name_tokens = re.sub(rf'\b{re.escape(sw)}\b', ' ', name_tokens)

    clean_item_name = " ".join(name_tokens.split()).strip()
    if not clean_item_name:
        clean_item_name = "Loose Grocery Item"

    clean_title = " ".join(w.capitalize() for w in clean_item_name.split())

    # Rate determination
    if spoken_price and spoken_price > 0:
        rate = spoken_price
    else:
        rate = 65.0
        for k_word, std_info in STANDARD_ITEMS.items():
            if k_word in clean_item_name.lower():
                rate = std_info["rate"]
                if not qty_regex and "unit" in std_info:
                    unit = std_info["unit"]
                break

    # Category determination
    lower_title = clean_title.lower()
    if any(k in lower_title for k in ["jeera", "cumin", "masala", "mirch", "haldi", "dhaniya", "clove", "elaichi", "spices", "rai", "methi"]):
        category = "Loose Groceries & Spices"
    elif "loose" in lower_title or "khula" in lower_title:
        category = "Loose Groceries & Spices"
    elif any(k in lower_title for k in ["atta", "flour", "besan", "maida", "suji", "chawal", "rice", "dal", "sugar", "cheeni", "salt", "poha"]):
        category = "Staples & Grains"
    elif any(k in lower_title for k in ["milk", "doodh", "paneer", "dahi", "butter", "ghee", "curd"]):
        category = "Dairy & Fresh"
    elif any(k in lower_title for k in ["biscuit", "chips", "maggi", "namkeen", "snack"]):
        category = "Snacks & Branded"
    elif any(k in lower_title for k in ["oil", "tel"]):
        category = "Cooking Oils"
    elif any(k in lower_title for k in ["tea", "chai", "coffee"]):
        category = "Beverages"
    elif any(k in lower_title for k in ["soap", "paste", "surf", "shampoo", "brush"]):
        category = "Personal Care & Cleaning"
    else:
        category = "General Kirana"

    calculated_expiry = None
    if expiry_days is not None:
        calculated_expiry = date.today() + timedelta(days=expiry_days)

    existing_item = db.query(Inventory).filter(
        Inventory.shopkeeper_id == target_shop_id,
        Inventory.is_active == True,
        (Inventory.item_name.ilike(clean_title) | Inventory.item_name.ilike(f"%{clean_title}%"))
    ).first()

    if existing_item:
        existing_item.stock_quantity += Decimal(str(qty))
        if spoken_price:
            existing_item.current_selling_price = Decimal(str(rate))
            existing_item.recommended_selling_price = Decimal(str(rate))
        if calculated_expiry:
            existing_item.expiry_date = calculated_expiry
        db.commit()
        db.refresh(existing_item)

        speech_text = f"'{existing_item.item_name}' stock increased by {qty:.0f} {existing_item.unit}. Total stock: {float(existing_item.stock_quantity):.0f} {existing_item.unit}."
        return {
            "status": "SUCCESS",
            "intent": "INVENTORY_ADDED",
            "intent_type": "INVENTORY_ADDITION",
            "action": "STOCK_IN",
            "is_auto_catalog": False,
            "item": {
                "item_id": existing_item.item_id,
                "item_name": existing_item.item_name,
                "category": existing_item.category,
                "unit": existing_item.unit,
                "added_quantity": qty,
                "new_stock_quantity": float(existing_item.stock_quantity),
                "selling_price": float(existing_item.current_selling_price),
                "expiry_date": existing_item.expiry_date.isoformat() if existing_item.expiry_date else None
            },
            "speech_response": speech_text
        }
    else:
        cost_p = Decimal(str(round(rate * 0.80, 2)))
        sell_p = Decimal(str(round(rate, 2)))
        new_item = Inventory(
            shopkeeper_id=target_shop_id,
            item_name=clean_title,
            sku=f"VOICE-{uuid.uuid4().hex[:6].upper()}",
            category=category,
            stock_quantity=Decimal(str(qty)),
            low_stock_threshold=Decimal("5.00"),
            unit=unit,
            purchase_price=cost_p,
            recommended_selling_price=sell_p,
            current_selling_price=sell_p,
            expiry_date=calculated_expiry,
            seasonal_category="All-Season",
            is_daily_need=True if any(k in lower_title for k in ["jeera", "sugar", "milk", "salt", "atta", "oil", "dal", "besan", "rice", "bread"]) else False,
            is_active=True
        )
        db.add(new_item)
        db.commit()
        db.refresh(new_item)

        speech_text = f"New item '{new_item.item_name}' added to custom Inventory & Quick-Tap Grid! Stock: {qty:.0f} {new_item.unit} at ₹{rate:.0f}/{new_item.unit}."
        return {
            "status": "SUCCESS",
            "intent": "INVENTORY_ADDED",
            "intent_type": "INVENTORY_ADDITION",
            "action": "AUTO_CATALOG",
            "is_auto_catalog": True,
            "item": {
                "item_id": new_item.item_id,
                "item_name": new_item.item_name,
                "category": new_item.category,
                "unit": new_item.unit,
                "stock_quantity": float(new_item.stock_quantity),
                "selling_price": float(new_item.current_selling_price),
                "expiry_date": new_item.expiry_date.isoformat() if new_item.expiry_date else None,
                "is_unbarcoded": True
            },
            "speech_response": speech_text
        }


@app.post("/api/inventory/voice-add", tags=["Inventory", "Voice POS"])
def inventory_voice_add(payload: VoiceCommandEntryRequest, db: Session = Depends(get_db)):
    """
    Dedicated Voice-Powered Stock Addition endpoint.
    Receives spoken natural language stock addition text (Hindi, Hinglish, or English),
    extracts item name, quantity, unit, price, and optional expiry,
    and saves/updates the item in the database inventory table so it reflects
    instantly in both the inventory list and the Quick-Tap Counter Grid.
    """
    raw = (payload.text or payload.raw_text or "").strip()
    if not raw:
        raise HTTPException(status_code=400, detail="Voice text input cannot be empty.")

    target_shop_id = payload.shopkeeper_id
    if not target_shop_id:
        first_sk = db.query(User).filter(User.role == "shopkeeper").first()
        target_shop_id = first_sk.user_id if first_sk else 1

    shopkeeper = db.query(User).filter(User.user_id == target_shop_id).first()
    if not shopkeeper:
        raise HTTPException(status_code=404, detail="Shopkeeper not found.")

    return handle_voice_stock_addition(raw, target_shop_id, db)


@app.post("/api/voice/process", tags=["Voice POS"])
@app.post("/api/entry/voice-command", tags=["Data Entry"])
def process_voice_command(payload: VoiceCommandEntryRequest, db: Session = Depends(get_db)):
    """
    Real-time browser speech recognition & NLP intent execution:
    1. 'added', 'stock in', 'laaya' -> Auto add/update custom Inventory & Quick-Tap Catalog
    2. 'given to', 'sold to', 'di' -> Deduct from inventory & log sale into sales_history
    3. 'udhaar', 'credit', 'likho' -> Record directly into customer's Khata ledger
    4. 'jama', 'payment' -> Accept cash payment against outstanding balance
    """
    raw = (payload.text or payload.raw_text or "").strip()
    if not raw:
        raise HTTPException(status_code=400, detail="Voice text input cannot be empty.")
    text_lower = raw.lower()

    # Target shopkeeper resolution
    target_shop_id = payload.shopkeeper_id
    if not target_shop_id:
        first_sk = db.query(User).filter(User.role == "shopkeeper").first()
        target_shop_id = first_sk.user_id if first_sk else 1

    shopkeeper = db.query(User).filter(User.user_id == target_shop_id).first()
    if not shopkeeper:
        raise HTTPException(status_code=404, detail="Shopkeeper not found.")

    # ------------------------------------------------------------------------
    # 1. SMART NLP INTENT CLASSIFICATION: 3 DISTINCT MERCHANT INTENTS
    # ------------------------------------------------------------------------
    # 0. Payment settlement receipt (Jama)
    is_payment_jama = (
        any(k in text_lower for k in ["jama", "payment mila", "paisa mila", "rupaye jama"]) or
        (any(k in text_lower for k in ["mila", "aaya", "aaye", "received", "paid"]) and not any(k in text_lower for k in ["laaya", "laya", "packet", "packets", "kg", "sugar", "doodh", "cheeni", "atta", "salt"]))
    )

    # Intent 2: Udhari / Khata Ledger Entry
    # Trigger keywords: "udhar", "credit", "likho", "khata", "dena hai", "hisaab"
    is_khata_intent = (
        bool(re.search(r'\b(?:udhaar|udhar|credit|likho|likh\s*do|likh\s*lo|likh|khata|khate|dena\s*hai|dena\s*h|hisaab|hisab|baaki|baki)\b', text_lower))
        or "khate me" in text_lower
        or "khata me" in text_lower
        or "dena hai" in text_lower
        or "ka hisaab" in text_lower
        or "ke hisaab" in text_lower
    ) and not is_payment_jama

    # Intent 1: Daily Sales / Cash Billing
    # Trigger keywords: "sold", "di", "bikri", "counter sale", "cash"
    is_sale_intent = (
        bool(re.search(r'\b(?:sold|sold\s+to|given\s+to|given|di|diya|bikri|bika|bik\s*gaya|counter\s*sale|cash|cash\s*sale|walk-in\s*cash|becha|bech\s+diya|bechi|sale)\b', text_lower))
        or any(k in text_lower for k in ["counter sale", "cash sale", "walk-in", "walkin", "walk in", "guest", "bikri"])
    ) and not is_khata_intent and not is_payment_jama

    # Intent 3: Stock Inward / Inventory Addition
    # Trigger keywords: "jodo", "add", "stock in", "laaya", "naya maal", "packet"
    is_inventory_intent = (
        bool(re.search(r'\b(?:jodo|jod\s*do|add|stock\s*in|laaya|laya|naya\s*maal|naya\s*stock|naye\s*maal|packet|packets|pkts|pkt|stock\s*add|stock\s*inward|add\s*karo|daalo|nayi\s*item|item\s*add|stock\s*badhao|inward)\b', text_lower))
        or (text_lower.startswith("add ") and not any(w in text_lower for w in ["credit", "udhar", "udhaar", "jama", "payment"]))
    ) and not is_khata_intent and not is_sale_intent and not is_payment_jama

    # ------------------------------------------------------------------------
    # 2. INTENT A: PAYMENT RECEIVED (JAMA)
    # ------------------------------------------------------------------------
    if is_payment_jama:
        amt_match = re.search(r'(?:₹|rs\.?|rupaye|rupees)?\s*(\d+(?:\.\d+)?)\s*(?:₹|rs\.?|rupaye|rupees)?', text_lower)
        words = [w for w in re.findall(r'[a-zA-Z\u0900-\u097F]+', text_lower) if w not in [
            "jama", "payment", "aaya", "aaye", "mila", "received", "paid", "rs", "rupaye", "rupees", "se", "ne", "ko", "ka", "ke"
        ]]
        cust_name = words[0].capitalize() if words else "Customer"
        amt = float(amt_match.group(1)) if amt_match else 0.0

        if amt > 0:
            khata = db.query(Khata).filter(
                Khata.shopkeeper_id == target_shop_id,
                Khata.customer_name.ilike(f"%{cust_name}%")
            ).first()

            if khata:
                amt_dec = Decimal(str(amt))
                bal_before = khata.current_balance
                bal_after = max(Decimal("0.00"), bal_before - amt_dec)
                khata.current_balance = bal_after
                khata.last_payment_date = datetime.utcnow()

                receipt_no = f"RCP-PAY-{int(time.time() * 1000) % 10000000}-{uuid.uuid4().hex[:4].upper()}"

                tx = KhataTransaction(
                    khata_id=khata.khata_id,
                    transaction_type="ACCEPT_PAYMENT",
                    amount=amt_dec,
                    balance_before=bal_before,
                    balance_after=bal_after,
                    note="Voice Payment Jama",
                    bill_invoice_no=receipt_no
                )
                db.add(tx)

                receipt = Receipt(
                    receipt_id=receipt_no,
                    shopkeeper_id=target_shop_id,
                    customer_id=khata.customer_id,
                    customer_name=khata.customer_name,
                    customer_phone=khata.customer_phone,
                    items_summary=json.dumps([{"name": "Khata Balance Payment", "qty": 1, "unit": "txn", "rate": amt, "total": amt}]),
                    subtotal=amt_dec,
                    discount=Decimal("0.00"),
                    total_amount=amt_dec,
                    payment_status="PAID_CASH",
                    source="VOICE_COMMAND_POS",
                    voice_transcript=raw
                )
                db.add(receipt)

                if khata.customer_id:
                    msg = Message(
                        sender_id=target_shop_id,
                        receiver_id=khata.customer_id,
                        shopkeeper_id=target_shop_id,
                        customer_id=khata.customer_id,
                        sender_role="shopkeeper",
                        content=f"💰 Payment Received: ₹{amt:.2f} received. Updated Khata balance: ₹{float(bal_after):.2f}.",
                        message_type="PAYMENT_ALERT"
                    )
                    db.add(msg)
                db.commit()

                return {
                    "intent": "ACCEPT_PAYMENT",
                    "status": "SUCCESS",
                    "receipt_id": receipt_no,
                    "speech_response": f"{khata.customer_name} se ₹{amt:.0f} jama kiye gaye. Naya baki balance ₹{float(bal_after):.0f} hai.",
                    "data": {"customer_name": khata.customer_name, "amount": amt, "new_balance": float(bal_after)}
                }

    # ------------------------------------------------------------------------
    # 3. INTENT B: INWARD STOCK / ADDED / LAAYA (INVENTORY & CATALOG ADD)
    # ------------------------------------------------------------------------
    if is_inventory_intent:
        return handle_voice_stock_addition(raw, target_shop_id, db)

    # ------------------------------------------------------------------------
    # 4. ITEM PARSER FOR SALES & KHATA ENTRIES
    # ------------------------------------------------------------------------
    db_inventory = db.query(Inventory).filter(
        Inventory.shopkeeper_id == target_shop_id,
        Inventory.is_active == True
    ).all()
    inv_map = {inv.item_name.lower(): inv for inv in db_inventory}

    item_tokens = re.split(r'\s+(?:and|aur|\+|\&)\s+|,', text_lower)
    parsed_items = []
    item_regex = re.compile(r'(\d+(?:\.\d+)?)\s*(packets|packet|pkts|pkt|litres|litre|ltr|l|bottles|bottle|pieces|piece|pcs|packs|pack|kilos|kilo|kg|grams|gram|gms|gm|g|bori|bags|bag|boxes|box)?\b\s*(?:of\s+)?([a-zA-Z\u0900-\u097F]+)')

    for segment in item_tokens:
        clean_seg = segment.strip()
        for prefix in ["given to", "given", "sold to", "sold", "to ", "for ", "on udhar", "on credit", "udhar likho", "credit likho", "counter sale", "cash sale", "bikri"]:
            if clean_seg.startswith(prefix):
                clean_seg = clean_seg[len(prefix):].strip()
        matches = item_regex.findall(clean_seg)
        for qty_str, unit_str, prod_word in matches:
            prod_word_clean = prod_word.lower().strip()
            if prod_word_clean in [
                "given", "sold", "to", "for", "on", "udhar", "udhaar", "credit", "rs", "rupaye", "rupees",
                "likho", "likh", "di", "diya", "cash", "bikri", "dena", "hai", "hisaab", "hisab",
                "khata", "khate", "baaki", "baki", "ko", "ka", "ke", "ki", "me", "se", "pe",
                "jama", "mila", "aaya", "aaye", "payment", "paisa"
            ]:
                continue
            qty = float(qty_str)
            unit = unit_str if unit_str else "pcs"

            # Check if an explicit price was spoken in the segment or command
            price_m = re.search(r'(?:at|@|rate|price|ke|bhav|rupaye|rupees|rs\.?|₹|me|for)\s*(\d+(?:\.\d+)?)', clean_seg)
            if not price_m:
                price_m = re.search(r'(\d+(?:\.\d+)?)\s*(?:rupees|rupaye|rs\.?|₹|cash)', clean_seg)
            spoken_rate = None
            if price_m:
                try:
                    c_val = float(price_m.group(1))
                    if c_val != qty:
                        spoken_rate = c_val
                except Exception:
                    pass

            matched_inv = None
            if len(prod_word_clean) >= 3:
                for inv_name, inv_obj in inv_map.items():
                    if prod_word_clean in inv_name:
                        matched_inv = inv_obj
                        break

            if not matched_inv and prod_word_clean in STANDARD_ITEMS:
                std_cand = STANDARD_ITEMS[prod_word_clean]["name"].lower()
                for inv_name, inv_obj in inv_map.items():
                    if any(k in inv_name for k in [std_cand, prod_word_clean]):
                        matched_inv = inv_obj
                        break

            if spoken_rate and spoken_rate > 0:
                rate = spoken_rate
                disp_name = matched_inv.item_name if matched_inv else prod_word_clean.capitalize()
                unit = matched_inv.unit if matched_inv else (unit_str or "pcs")
                if matched_inv:
                    matched_inv.stock_quantity = max(Decimal("0.00"), matched_inv.stock_quantity - Decimal(str(qty)))
            elif matched_inv:
                rate = float(matched_inv.current_selling_price)
                disp_name = matched_inv.item_name
                unit = matched_inv.unit
                # Deduct inventory
                matched_inv.stock_quantity = max(Decimal("0.00"), matched_inv.stock_quantity - Decimal(str(qty)))
            elif prod_word_clean in STANDARD_ITEMS:
                std = STANDARD_ITEMS[prod_word_clean]
                rate = std["rate"]
                disp_name = std["name"]
                unit = unit_str or std["unit"]
            else:
                rate = 40.0
                disp_name = prod_word_clean.capitalize()
                unit = unit_str or "pcs"

            subtotal = round(qty * rate, 2)
            parsed_items.append({
                "name": disp_name,
                "qty": qty,
                "unit": unit,
                "rate": rate,
                "subtotal": subtotal,
                "item_id": matched_inv.item_id if matched_inv else None,
                "stock_deducted": True if matched_inv else False
            })

        if not matches:
            words = [w.lower().strip() for w in re.findall(r'[a-zA-Z\u0900-\u097F]+', clean_seg)]
            for w in words:
                if w in ["given", "sold", "to", "for", "on", "udhar", "udhaar", "credit", "rs", "rupaye", "rupees", "likho", "likh", "di", "diya", "becha", "bechi", "bech", "ko", "ka", "ke", "cash", "bikri", "counter", "sale"]:
                    continue
                matched_inv = None
                for inv_name, inv_obj in inv_map.items():
                    if w in inv_name:
                        matched_inv = inv_obj
                        break
                if not matched_inv and w in STANDARD_ITEMS:
                    std_cand = STANDARD_ITEMS[w]["name"].lower()
                    for inv_name, inv_obj in inv_map.items():
                        if any(k in inv_name for k in [std_cand, w]):
                            matched_inv = inv_obj
                            break

                if matched_inv or w in STANDARD_ITEMS:
                    # Check spoken rate
                    price_m = re.search(r'(?:at|@|rate|price|ke|bhav|rupaye|rupees|rs\.?|₹|me|for)\s*(\d+(?:\.\d+)?)', clean_seg)
                    if not price_m:
                        price_m = re.search(r'(\d+(?:\.\d+)?)\s*(?:rupees|rupaye|rs\.?|₹|cash)', clean_seg)
                    spoken_rate = None
                    if price_m:
                        try:
                            spoken_rate = float(price_m.group(1))
                        except Exception:
                            pass

                    qty = 1.0
                    if spoken_rate and spoken_rate > 0:
                        rate = spoken_rate
                        disp_name = matched_inv.item_name if matched_inv else (STANDARD_ITEMS[w]["name"] if w in STANDARD_ITEMS else w.capitalize())
                        unit = matched_inv.unit if matched_inv else (STANDARD_ITEMS[w]["unit"] if w in STANDARD_ITEMS else "pcs")
                        if matched_inv:
                            matched_inv.stock_quantity = max(Decimal("0.00"), matched_inv.stock_quantity - Decimal(str(qty)))
                    elif matched_inv:
                        rate = float(matched_inv.current_selling_price)
                        disp_name = matched_inv.item_name
                        unit = matched_inv.unit
                        matched_inv.stock_quantity = max(Decimal("0.00"), matched_inv.stock_quantity - Decimal(str(qty)))
                    else:
                        std = STANDARD_ITEMS[w]
                        rate = std["rate"]
                        disp_name = std["name"]
                        unit = std["unit"]

                    parsed_items.append({
                        "name": disp_name,
                        "qty": qty,
                        "unit": unit,
                        "rate": rate,
                        "subtotal": round(qty * rate, 2),
                        "item_id": matched_inv.item_id if matched_inv else None,
                        "stock_deducted": True if matched_inv else False
                    })
                    break

    # ------------------------------------------------------------------------
    # 5. INTENT C: COMPLETED SALE / GIVEN TO / SOLD TO / DI -> SALES HISTORY
    # ------------------------------------------------------------------------
    if is_sale_intent:
        NON_NAME_WORDS = {
            "sugar", "cheeni", "chini", "milk", "doodh", "dudh", "bread", "atta", "oil", "tel", "tea", "chai",
            "dal", "rice", "chawal", "cash", "packet", "packets", "pkt", "pkts", "kg", "kilos", "kilo",
            "gram", "grams", "gms", "gm", "g", "pcs", "piece", "pieces", "bottle", "bottles", "litre",
            "litres", "l", "loose", "namkeen", "biscuit", "biscuits", "soap", "sabun", "salt", "namak",
            "jeera", "cumin", "mirch", "haldi", "spices", "item", "items", "stock", "di", "diya", "becha",
            "sold", "given", "walk-in", "card", "online", "rupaye", "rupees", "rs", "one", "two", "three",
            "ten", "ek", "do", "teen", "char", "paanch", "das"
        }

        cust_name = None
        # Hindi pattern: "<Name> ko ..."
        ko_match = re.search(r'\b([a-zA-Z\u0900-\u097F]+)\s+ko\b', text_lower)
        if ko_match:
            cand = ko_match.group(1).capitalize()
            if cand.lower() not in NON_NAME_WORDS and cand.lower() not in inv_map:
                cust_name = cand

        # English pattern: "sold to <Name>", "given to <Name>"
        if not cust_name:
            target_match = re.search(r'(?:sold\s+to|given\s+to)\s+([a-zA-Z\u0900-\u097F]+)', text_lower)
            if target_match:
                cand = target_match.group(1).capitalize()
                if cand.lower() not in NON_NAME_WORDS and cand.lower() not in inv_map:
                    cust_name = cand

        # English preposition: "to <Name>", "for <Name>"
        if not cust_name:
            cand_to = re.search(r'\b(?:to|for)\s+([a-zA-Z\u0900-\u097F]+)', text_lower)
            if cand_to:
                cand = cand_to.group(1).capitalize()
                if cand.lower() not in NON_NAME_WORDS and cand.lower() not in inv_map:
                    cust_name = cand

        # Match existing Khata customers
        if not cust_name:
            for k in db.query(Khata).filter(Khata.shopkeeper_id == target_shop_id).all():
                if k.customer_name and re.search(rf'\b{re.escape(k.customer_name.lower())}\b', text_lower):
                    cust_name = k.customer_name
                    break

        # Fallback Hindi pattern: "<Name> di/diya/becha"
        if not cust_name:
            hindi_target = re.search(r'\b([a-zA-Z\u0900-\u097F]+)\s*(?:ko)?\s*(?:di|diya|becha)\b', text_lower)
            if hindi_target:
                cand = hindi_target.group(1).capitalize()
                if cand.lower() not in NON_NAME_WORDS and cand.lower() not in inv_map:
                    cust_name = cand

        if not cust_name:
            if any(k in text_lower for k in ["guest", "privacy", "walk-in", "walk in"]):
                cust_name = "Walk-In Customer (Privacy Protected)"
            else:
                cust_name = "Walk-In Customer"

        if not parsed_items:
            amt_match = re.search(r'(?:₹|rs\.?|rupaye|rupees)?\s*(\d+(?:\.\d+)?)\s*(?:₹|rs\.?|rupaye|rupees)?', text_lower)
            if amt_match:
                amt = float(amt_match.group(1))
                parsed_items.append({
                    "name": "General Grocery Sale",
                    "qty": 1.0,
                    "unit": "order",
                    "rate": amt,
                    "subtotal": amt
                })
            else:
                parsed_items.append({
                    "name": "Daily Essentials Sale",
                    "qty": 1.0,
                    "unit": "pack",
                    "rate": 60.0,
                    "subtotal": 60.0
                })

        total_amount = sum(i["subtotal"] for i in parsed_items)
        total_dec = Decimal(str(round(total_amount, 2)))
        payment_mode = "UPI" if any(k in text_lower for k in ["upi", "online", "gpay", "phonepe"]) else "CASH"

        receipt_no = f"RCP-SALE-{int(time.time() * 1000) % 10000000}-{uuid.uuid4().hex[:4].upper()}"

        matched_cust = None
        if cust_name != "Walk-In Customer":
            matched_cust = db.query(User).filter(User.full_name.ilike(f"%{cust_name}%"), User.role == "customer").first()

        items_desc = ", ".join([f"{it['qty']}x {it['name']}" for it in parsed_items])
        sale = SalesHistory(
            shopkeeper_id=target_shop_id,
            customer_id=matched_cust.user_id if matched_cust else None,
            customer_name=cust_name,
            items_summary=items_desc,
            total_amount=total_dec,
            discount_amount=Decimal("0.00"),
            final_amount=total_dec,
            payment_mode=payment_mode,
            sale_date=datetime.utcnow()
        )
        db.add(sale)
        db.flush()

        for it in parsed_items:
            inv_item = None
            if it.get("item_id"):
                inv_item = db.query(Inventory).filter(Inventory.item_id == it["item_id"]).first()
            if not inv_item:
                inv_item = db.query(Inventory).filter(
                    Inventory.shopkeeper_id == target_shop_id,
                    Inventory.item_name.ilike(it["name"])
                ).first()

            if inv_item and not it.get("stock_deducted"):
                inv_item.stock_quantity = max(Decimal("0.00"), inv_item.stock_quantity - Decimal(str(it["qty"])))
                it["stock_deducted"] = True

            shi = SalesHistoryItem(
                sale_id=sale.sale_id,
                item_id=inv_item.item_id if inv_item else None,
                item_name=it["name"],
                quantity=Decimal(str(it["qty"])),
                unit_price=Decimal(str(it["rate"])),
                purchase_price=inv_item.purchase_price if inv_item else Decimal("0.00"),
                subtotal=Decimal(str(it["subtotal"]))
            )
            db.add(shi)

        receipt = Receipt(
            receipt_id=receipt_no,
            shopkeeper_id=target_shop_id,
            customer_id=matched_cust.user_id if matched_cust else None,
            customer_name=cust_name,
            customer_phone=matched_cust.phone_number if matched_cust else None,
            items_summary=json.dumps(parsed_items),
            subtotal=total_dec,
            discount=Decimal("0.00"),
            total_amount=total_dec,
            payment_status=f"PAID_{payment_mode}",
            source="VOICE_COMMAND_POS",
            voice_transcript=raw,
            is_viewed=True
        )
        db.add(receipt)
        db.commit()

        speech_text = f"Sale of ₹{total_amount:.0f} recorded for {cust_name}. Inventory updated!"

        is_guest_sale = bool(
            "guest" in text_lower or 
            "walk-in" in text_lower or 
            "walk in" in text_lower or 
            "privacy" in text_lower or 
            cust_name in ["Walk-In Customer", "Walk-In Customer (Privacy Protected)"]
        )

        return {
            "status": "SUCCESS",
            "intent": "SALE_LOGGED",
            "intent_type": "DAILY_SALES",
            "action": "DEDUCT_INVENTORY_AND_LOG_SALE",
            "sale_id": sale.sale_id,
            "receipt_id": receipt_no,
            "customer_name": cust_name,
            "customer_id": matched_cust.user_id if matched_cust else None,
            "is_guest": is_guest_sale,
            "items": parsed_items,
            "subtotal": total_amount,
            "total_amount": total_amount,
            "payment_mode": payment_mode,
            "payment_status": f"PAID_{payment_mode}",
            "speech_response": speech_text,
            "receipt": {
                "receipt_id": receipt_no,
                "shop_name": shopkeeper.shop_name or f"{shopkeeper.full_name}'s Kirana",
                "customer_name": cust_name,
                "customer_phone": matched_cust.phone_number if matched_cust else "N/A",
                "items": parsed_items,
                "total_amount": total_amount,
                "payment_status": f"PAID_{payment_mode}",
                "created_at": receipt.created_at.isoformat()
            }
        }

    # ------------------------------------------------------------------------
    # 6. INTENT D: KHATA LEDGER / UDHAAR / CREDIT / LIKHO / DENA HAI / HISAAB
    # ------------------------------------------------------------------------
    if is_khata_intent:
        KHATA_NON_NAME_WORDS = {
            "sugar", "cheeni", "milk", "doodh", "bread", "atta", "oil", "tel", "tea", "dal", "rice",
            "cash", "udhar", "udhaar", "credit", "packet", "packets", "pkt", "pkts", "kg", "kilos",
            "pcs", "piece", "rupaye", "rupees", "rs", "add", "likho", "likh", "do", "khata", "khate",
            "dena", "hai", "hisaab", "hisab", "baaki", "baki", "se", "ko", "ka", "ki", "ke", "me", "pe",
            "counter", "bikri", "sale"
        }
        cust_name = None

        # 1. Match against existing Khata customer records first!
        for k in db.query(Khata).filter(Khata.shopkeeper_id == target_shop_id).all():
            if k.customer_name and re.search(rf'\b{re.escape(k.customer_name.lower())}\b', text_lower):
                cust_name = k.customer_name
                break

        # 2. Hindi: "<Name> ko ..." (e.g. "Ramesh ko 500 dena hai", "Suresh ko 200 udhar likho")
        if not cust_name:
            ko_match = re.search(r'\b([a-zA-Z\u0900-\u097F]+)\s+ko\b', text_lower)
            if ko_match:
                cand = ko_match.group(1).capitalize()
                if cand.lower() not in KHATA_NON_NAME_WORDS and cand.lower() not in inv_map:
                    cust_name = cand

        # 3. Hindi: "<Name> ka/ke khate/hisaab ..." (e.g. "Ramesh ka hisaab 200", "Suresh ke khate me 300")
        if not cust_name:
            khate_match = re.search(r'\b([a-zA-Z\u0900-\u097F]+)\s+(?:ka|ke|ki)\s+(?:khate|khata|hisaab|hisab)', text_lower)
            if khate_match:
                cand = khate_match.group(1).capitalize()
                if cand.lower() not in KHATA_NON_NAME_WORDS and cand.lower() not in inv_map:
                    cust_name = cand

        # 4. English: "credit for <Name>", "for <Name>", "to <Name>"
        if not cust_name:
            credit_for = re.search(r'(?:credit\s+for|for|to)\s+([a-zA-Z\u0900-\u097F]+)', text_lower)
            if credit_for:
                cand = credit_for.group(1).capitalize()
                if cand.lower() not in KHATA_NON_NAME_WORDS and cand.lower() not in inv_map:
                    cust_name = cand

        # 5. Hindi: "<Name> (ko) ... dena hai/hisaab"
        if not cust_name:
            dena_match = re.search(r'\b([a-zA-Z\u0900-\u097F]+)\s*(?:ko)?\s*(?:₹|rs\.?|rupaye|rupees)?\s*(\d+(?:\.\d+)?)?\s*(?:₹|rs\.?|rupaye|rupees)?\s*(?:dena\s*hai|dena\s*h|hisaab|hisab)', text_lower)
            if dena_match:
                cand = dena_match.group(1).capitalize()
                if cand.lower() not in KHATA_NON_NAME_WORDS and cand.lower() not in inv_map:
                    cust_name = cand

        # 6. Generic fallback regex: "<Name> ... udhaar/credit/likho/dena hai/hisaab"
        if not cust_name:
            name_match = re.search(r'([a-zA-Z\u0900-\u097F]+)\s*(?:ko|ka|ke)?\s*(?:khate\s*me|khata)?\s*(?:₹|rs\.?|rupaye|rupees)?\s*(\d+(?:\.\d+)?)?\s*(?:₹|rs\.?|rupaye|rupees)?\s*(?:udhaar|udhar|credit|likho|likh\s*do|likh|dena\s*hai|hisaab|hisab)', text_lower)
            if name_match:
                cand = name_match.group(1).capitalize()
                if cand.lower() not in KHATA_NON_NAME_WORDS and cand.lower() not in inv_map:
                    cust_name = cand

        if not cust_name:
            cust_name = "Customer"

        amt_match = re.search(r'(?:₹|rs\.?|rupaye|rupees)?\s*(\d+(?:\.\d+)?)\s*(?:₹|rs\.?|rupaye|rupees)?', text_lower)
        if amt_match and not parsed_items:
            total_amount = float(amt_match.group(1))
            parsed_items.append({
                "name": "Khata Udhaar Entry",
                "qty": 1.0,
                "unit": "entry",
                "rate": total_amount,
                "subtotal": total_amount
            })
        elif parsed_items:
            total_amount = sum(i["subtotal"] for i in parsed_items)
        elif amt_match:
            total_amount = float(amt_match.group(1))
        else:
            total_amount = 100.0

        total_dec = Decimal(str(round(total_amount, 2)))

        customer = db.query(User).filter(
            User.full_name.ilike(f"%{cust_name}%"),
            User.role == "customer"
        ).first()

        if not customer:
            clean_phone = f"9893{abs(hash(cust_name)) % 900000 + 100000}"
            customer = User(
                full_name=cust_name,
                phone_number=clean_phone,
                role="customer",
                address="Civil Lines, Mandsaur",
                pin="1234",
                is_active=True
            )
            db.add(customer)
            db.commit()
            db.refresh(customer)

        khata = db.query(Khata).filter(
            Khata.shopkeeper_id == target_shop_id,
            (Khata.customer_id == customer.user_id) | (Khata.customer_name.ilike(f"%{cust_name}%"))
        ).first()

        if not khata:
            khata = Khata(
                shopkeeper_id=target_shop_id,
                customer_id=customer.user_id,
                customer_name=customer.full_name,
                customer_phone=customer.phone_number,
                current_balance=Decimal("0.00"),
                billing_reminder_date=date.today() + timedelta(days=15)
            )
            db.add(khata)
            db.commit()
            db.refresh(khata)

        bal_before = khata.current_balance
        bal_after = bal_before + total_dec
        khata.current_balance = bal_after

        receipt_no = f"RCP-UDHAR-{int(time.time() * 1000) % 10000000}-{uuid.uuid4().hex[:4].upper()}"

        tx = KhataTransaction(
            khata_id=khata.khata_id,
            transaction_type="GIVE_CREDIT",
            amount=total_dec,
            balance_before=bal_before,
            balance_after=bal_after,
            note=f"Voice Udhaar Entry #{receipt_no}",
            bill_invoice_no=receipt_no
        )
        db.add(tx)

        items_desc = ", ".join([f"{i['qty']}x {i['name']}" for i in parsed_items]) if parsed_items else f"Udhaar ₹{total_amount:.0f}"
        sale = SalesHistory(
            shopkeeper_id=target_shop_id,
            customer_id=customer.user_id,
            customer_name=customer.full_name,
            items_summary=items_desc,
            total_amount=total_dec,
            discount_amount=Decimal("0.00"),
            final_amount=total_dec,
            payment_mode="KHATA_CREDIT",
            sale_date=datetime.utcnow()
        )
        db.add(sale)
        db.flush()

        for it in parsed_items:
            inv_item = None
            if it.get("item_id"):
                inv_item = db.query(Inventory).filter(Inventory.item_id == it["item_id"]).first()
            if not inv_item:
                inv_item = db.query(Inventory).filter(
                    Inventory.shopkeeper_id == target_shop_id,
                    Inventory.item_name.ilike(it["name"])
                ).first()

            if inv_item and not it.get("stock_deducted"):
                inv_item.stock_quantity = max(Decimal("0.00"), inv_item.stock_quantity - Decimal(str(it["qty"])))
                it["stock_deducted"] = True

            shi = SalesHistoryItem(
                sale_id=sale.sale_id,
                item_id=inv_item.item_id if inv_item else None,
                item_name=it["name"],
                quantity=Decimal(str(it["qty"])),
                unit_price=Decimal(str(it["rate"])),
                purchase_price=inv_item.purchase_price if inv_item else Decimal("0.00"),
                subtotal=Decimal(str(it["subtotal"]))
            )
            db.add(shi)

        receipt = Receipt(
            receipt_id=receipt_no,
            shopkeeper_id=target_shop_id,
            customer_id=customer.user_id,
            customer_name=customer.full_name,
            customer_phone=customer.phone_number,
            items_summary=json.dumps(parsed_items),
            subtotal=total_dec,
            discount=Decimal("0.00"),
            total_amount=total_dec,
            payment_status="ADDED_TO_KHATA",
            source="VOICE_COMMAND_POS",
            voice_transcript=raw,
            is_viewed=False
        )
        db.add(receipt)

        chat_msg = Message(
            sender_id=target_shop_id,
            receiver_id=customer.user_id,
            shopkeeper_id=target_shop_id,
            customer_id=customer.user_id,
            sender_role="shopkeeper",
            content=f"🧾 Digital Khata Entry #{receipt_no}: {items_desc}. Total: ₹{total_amount:.2f} added to your account.",
            message_type="RECEIPT_ALERT",
            is_read=False
        )
        db.add(chat_msg)
        db.commit()

        limit_info = check_credit_limit_status(
            current_balance=float(bal_after),
            credit_limit=float(khata.credit_limit or 2000.0),
            customer_name=customer.full_name
        )

        speech_text = f"₹{total_amount:.0f} udhaar recorded for {khata.customer_name}. Naya Khata balance: ₹{float(bal_after):.0f}."
        if limit_info["credit_limit_exceeded"]:
            speech_text = f"⚠️ Warning: Credit cap exceeded! ₹{total_amount:.0f} udhaar added for {khata.customer_name}. Total dues ₹{float(bal_after):.0f} exceed ₹{float(khata.credit_limit or 2000):.0f} limit!"

        return {
            "status": "SUCCESS",
            "intent": "KHATA_CREDIT_RECORDED",
            "intent_type": "KHATA_UDHARI",
            "action": "RECORD_KHATA_CREDIT",
            "customer_name": khata.customer_name,
            "amount": float(total_dec),
            "new_balance": float(bal_after),
            "receipt_id": receipt_no,
            "credit_limit": float(khata.credit_limit or 2000.0),
            "credit_limit_status": limit_info,
            "credit_limit_exceeded": limit_info["credit_limit_exceeded"],
            "credit_limit_approaching": limit_info["credit_limit_approaching"],
            "demand_cash_required": limit_info["demand_cash_required"],
            "guarantor_name": khata.guarantor_name,
            "guarantor_phone": khata.guarantor_phone,
            "speech_response": speech_text,
            "receipt": {
                "receipt_id": receipt_no,
                "shop_name": shopkeeper.shop_name or f"{shopkeeper.full_name}'s Kirana",
                "customer_name": customer.full_name,
                "customer_phone": customer.phone_number,
                "items": parsed_items,
                "total_amount": total_amount,
                "payment_status": "ADDED_TO_KHATA",
                "created_at": receipt.created_at.isoformat()
            }
        }

    # ------------------------------------------------------------------------
    # 7. GENERAL POS FALLBACK
    # ------------------------------------------------------------------------
    final_items = list(parsed_items)
    if not final_items:
        # Check if any inventory or standard item was mentioned in text_lower
        words = [w.lower().strip() for w in re.findall(r'[a-zA-Z\u0900-\u097F]+', text_lower)]
        for w in words:
            if w in ["pos", "counter", "sale", "cash", "rupaye", "rupees", "rs", "ki", "ka", "ke", "hai", "me", "do", "karo"]:
                continue
            matched_inv = None
            for inv_name, inv_obj in inv_map.items():
                if w in inv_name:
                    matched_inv = inv_obj
                    break
            if not matched_inv and w in STANDARD_ITEMS:
                std_cand = STANDARD_ITEMS[w]["name"].lower()
                for inv_name, inv_obj in inv_map.items():
                    if any(k in inv_name for k in [std_cand, w]):
                        matched_inv = inv_obj
                        break
            if matched_inv or w in STANDARD_ITEMS:
                if matched_inv:
                    rate = float(matched_inv.current_selling_price)
                    disp_name = matched_inv.item_name
                    unit = matched_inv.unit
                    matched_inv.stock_quantity = max(Decimal("0.00"), matched_inv.stock_quantity - Decimal("1.00"))
                else:
                    std = STANDARD_ITEMS[w]
                    rate = std["rate"]
                    disp_name = std["name"]
                    unit = std["unit"]

                final_items.append({
                    "name": disp_name,
                    "qty": 1.0,
                    "unit": unit,
                    "rate": rate,
                    "subtotal": rate,
                    "item_id": matched_inv.item_id if matched_inv else None,
                    "stock_deducted": True if matched_inv else False
                })
                break

    amt_match = re.search(r'(?:₹|rs\.?|rupaye|rupees)?\s*(\d+(?:\.\d+)?)\s*(?:₹|rs\.?|rupaye|rupees)?', text_lower)
    if final_items:
        amt = sum(i["subtotal"] for i in final_items)
        items_summary_str = ", ".join([f"{i['qty']:.0f}x {i['name']}" for i in final_items])
    elif amt_match:
        amt = float(amt_match.group(1))
        items_summary_str = "Daily Grocery Essentials"
        final_items.append({
            "name": "Daily Grocery Essentials",
            "qty": 1.0,
            "unit": "sale",
            "rate": amt,
            "subtotal": amt,
            "item_id": None,
            "stock_deducted": False
        })
    else:
        amt = 50.0
        items_summary_str = "Daily Grocery Essentials"
        final_items.append({
            "name": "Daily Grocery Essentials",
            "qty": 1.0,
            "unit": "sale",
            "rate": 50.0,
            "subtotal": 50.0,
            "item_id": None,
            "stock_deducted": False
        })

    total_dec = Decimal(str(round(amt, 2)))
    receipt_no = f"RCP-POS-{int(time.time() * 1000) % 10000000}-{uuid.uuid4().hex[:4].upper()}"

    is_guest_mode = bool("guest" in text_lower or "walk-in" in text_lower or "walk in" in text_lower or "privacy" in text_lower)
    display_cust_name = "Walk-In Customer (Privacy Protected)" if is_guest_mode else "Walk-In Customer"

    sale = SalesHistory(
        shopkeeper_id=target_shop_id,
        customer_id=None,
        customer_name=display_cust_name,
        items_summary=items_summary_str,
        total_amount=total_dec,
        discount_amount=Decimal("0.00"),
        final_amount=total_dec,
        payment_mode="CASH",
        sale_date=datetime.utcnow()
    )
    db.add(sale)
    db.flush()

    for it in final_items:
        inv_item = None
        if it.get("item_id"):
            inv_item = db.query(Inventory).filter(Inventory.item_id == it["item_id"]).first()
        if not inv_item:
            inv_item = db.query(Inventory).filter(
                Inventory.shopkeeper_id == target_shop_id,
                Inventory.item_name.ilike(it["name"])
            ).first()

        shi = SalesHistoryItem(
            sale_id=sale.sale_id,
            item_id=inv_item.item_id if inv_item else None,
            item_name=it["name"],
            quantity=Decimal(str(it["qty"])),
            unit_price=Decimal(str(it["rate"])),
            purchase_price=inv_item.purchase_price if inv_item else Decimal("0.00"),
            subtotal=Decimal(str(it["subtotal"]))
        )
        db.add(shi)

    db.commit()

    return {
        "status": "SUCCESS",
        "intent": "SALE_LOGGED",
        "intent_type": "DAILY_SALES",
        "action": "COUNTER_SALE",
        "sale_id": sale.sale_id,
        "receipt_id": receipt_no,
        "total_amount": amt,
        "items": final_items,
        "customer_name": display_cust_name,
        "customer_id": None,
        "is_guest": is_guest_mode,
        "speech_response": f"Sale of ₹{amt:.0f} recorded for {items_summary_str}.",
        "receipt": {
            "receipt_id": receipt_no,
            "shop_name": shopkeeper.shop_name or "Kirana Store",
            "customer_name": display_cust_name,
            "items": final_items,
            "total_amount": amt,
            "payment_status": "PAID_CASH",
            "created_at": datetime.utcnow().isoformat()
        }
    }


@app.post("/api/entry/barcode-scan", tags=["Data Entry"])
def process_barcode_scan(payload: BarcodeScanRequest, db: Session = Depends(get_db)):
    """Scans and updates barcodes strictly for the shopkeeper's store."""
    item = db.query(Inventory).filter(
        Inventory.shopkeeper_id == payload.shopkeeper_id,
        (Inventory.sku.ilike(payload.barcode.strip())) | (Inventory.item_name.ilike(f"%{payload.barcode.strip()}%"))
    ).first()

    if not item:
        return {
            "status": "NOT_FOUND",
            "message": f"No item in your inventory matching barcode: '{payload.barcode}'",
            "barcode": payload.barcode
        }

    if payload.action == "STOCK_IN":
        qty_add = Decimal(str(payload.quantity))
        item.stock_quantity += qty_add
        db.commit()
        db.refresh(item)
        return {
            "status": "STOCK_UPDATED",
            "item_name": item.item_name,
            "added_quantity": float(qty_add),
            "new_stock_quantity": float(item.stock_quantity)
        }

    return {
        "status": "FOUND",
        "item": {
            "item_id": item.item_id,
            "item_name": item.item_name,
            "sku": item.sku,
            "category": item.category,
            "stock_quantity": float(item.stock_quantity),
            "unit": item.unit,
            "current_selling_price": float(item.current_selling_price),
            "selling_price": float(item.current_selling_price),
            "price": float(item.current_selling_price)
        }
    }


# ============================================================================
# 7.5 SALES HISTORY & ANALYTICS DASHBOARD API
# ============================================================================

@app.get("/api/sales/history", tags=["Sales & Analytics"])
def get_sales_history(
    shopkeeper_id: int = Query(...),
    period: Optional[str] = Query("all", description="today, week, month, all"),
    query: Optional[str] = Query(None, description="Search by item name or customer name"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db)
):
    """
    Fetches completed sales records for the shopkeeper with time-period and product search filters.
    Includes item details, quantities, total prices, customer info, and timestamps.
    """
    now = datetime.utcnow()
    q = db.query(SalesHistory).filter(SalesHistory.shopkeeper_id == shopkeeper_id)

    period_normalized = (period or "all").lower().strip()
    if period_normalized == "today":
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        q = q.filter(SalesHistory.sale_date >= today_start)
    elif period_normalized == "week":
        week_start = now - timedelta(days=7)
        q = q.filter(SalesHistory.sale_date >= week_start)
    elif period_normalized == "month":
        month_start = now - timedelta(days=30)
        q = q.filter(SalesHistory.sale_date >= month_start)

    if query and query.strip():
        search_term = f"%{query.strip()}%"
        # Match either items_summary, customer_name, or items in sales_history_items
        matching_sale_ids = db.query(SalesHistoryItem.sale_id).filter(
            SalesHistoryItem.item_name.ilike(search_term)
        ).scalar_subquery()
        q = q.filter(
            or_(
                SalesHistory.items_summary.ilike(search_term),
                SalesHistory.customer_name.ilike(search_term),
                SalesHistory.sale_id.in_(matching_sale_ids)
            )
        )

    total_count = q.count()
    sales_records = q.order_by(SalesHistory.sale_date.desc()).offset(offset).limit(limit).all()

    results = []
    total_rev = 0.0
    total_items = 0.0

    for s in sales_records:
        total_rev += float(s.final_amount)
        items_list = []
        qty_sum = 0.0
        primary_name = None

        for item in s.items:
            it_name = item.item_name or (item.item.item_name if item.item else "General Item")
            if not primary_name:
                primary_name = it_name
            it_qty = float(item.quantity)
            qty_sum += it_qty
            items_list.append({
                "sale_item_id": item.sale_item_id,
                "item_id": item.item_id,
                "item_name": it_name,
                "quantity": it_qty,
                "unit_price": float(item.unit_price),
                "subtotal": float(item.subtotal)
            })

        if not items_list and s.items_summary:
            summary_clean = s.items_summary.strip()
            if any(k in summary_clean.lower() for k in ["counter sale", "walk-in cash billing", "guest billing"]):
                primary_name = "Daily Grocery Essentials"
                qty_sum = 1.0
                items_list.append({
                    "sale_item_id": None,
                    "item_id": None,
                    "item_name": primary_name,
                    "quantity": 1.0,
                    "unit_price": float(s.final_amount),
                    "subtotal": float(s.final_amount)
                })
            else:
                for part in re.split(r'[,+&]|\band\b', summary_clean):
                    part_str = part.strip()
                    m = re.match(r'(?:(\d+(?:\.\d+)?)\s*[xX]?\s*)?(.+)', part_str)
                    if m:
                        q = float(m.group(1)) if m.group(1) else 1.0
                        n = m.group(2).strip()
                        if n:
                            qty_sum += q
                            if not primary_name:
                                primary_name = n
                            items_list.append({
                                "sale_item_id": None,
                                "item_id": None,
                                "item_name": n,
                                "quantity": q,
                                "unit_price": round(float(s.final_amount) / q if q > 0 else float(s.final_amount), 2),
                                "subtotal": float(s.final_amount)
                            })

        total_items += qty_sum if qty_sum > 0 else 1.0
        item_name_display = primary_name or (s.items_summary if s.items_summary else "Daily Grocery Essentials")
        quantity_display = qty_sum if qty_sum > 0 else 1.0
        primary_unit_price = items_list[0]["unit_price"] if items_list else float(s.final_amount)

        results.append({
            "id": s.sale_id,
            "sale_id": s.sale_id,
            "shop_id": s.shopkeeper_id,
            "shopkeeper_id": s.shopkeeper_id,
            "customer_name": s.customer_name or "Walk-In Customer",
            "customer_id": s.customer_id,
            "item_name": item_name_display,
            "quantity": quantity_display,
            "unit_price": primary_unit_price,
            "total_price": float(s.final_amount),
            "payment_mode": s.payment_mode,
            "items_summary": s.items_summary or item_name_display,
            "timestamp": s.sale_date.isoformat(),
            "sale_date": s.sale_date.strftime("%d %b %Y, %I:%M %p"),
            "items": items_list
        })

    return {
        "status": "SUCCESS",
        "shopkeeper_id": shopkeeper_id,
        "period": period_normalized,
        "total_records": total_count,
        "total_revenue": round(total_rev, 2),
        "total_items_sold": round(total_items, 1),
        "sales": results
    }


@app.get("/api/sales/analytics", tags=["Sales & Analytics"])
def get_sales_analytics(shopkeeper_id: int = Query(...), db: Session = Depends(get_db)):
    """
    Returns high-level sales metrics including today's sales, weekly & monthly aggregate revenue,
    total items sold, and top-performing products.
    """
    now = datetime.utcnow()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = now - timedelta(days=7)
    month_start = now - timedelta(days=30)

    # Today
    today_sales = db.query(SalesHistory).filter(
        SalesHistory.shopkeeper_id == shopkeeper_id,
        SalesHistory.sale_date >= today_start
    ).all()
    today_revenue = sum(float(s.final_amount) for s in today_sales)
    today_tx_count = len(today_sales)
    today_items_sold = sum(
        sum(float(i.quantity) for i in s.items) if s.items else 1.0
        for s in today_sales
    )

    # Weekly
    week_sales = db.query(SalesHistory).filter(
        SalesHistory.shopkeeper_id == shopkeeper_id,
        SalesHistory.sale_date >= week_start
    ).all()
    weekly_revenue = sum(float(s.final_amount) for s in week_sales)
    weekly_tx_count = len(week_sales)

    # Monthly
    month_sales = db.query(SalesHistory).filter(
        SalesHistory.shopkeeper_id == shopkeeper_id,
        SalesHistory.sale_date >= month_start
    ).all()
    monthly_revenue = sum(float(s.final_amount) for s in month_sales)
    monthly_tx_count = len(month_sales)
    monthly_items_sold = sum(
        sum(float(i.quantity) for i in s.items) if s.items else 1.0
        for s in month_sales
    )

    # All-time
    all_sales = db.query(SalesHistory).filter(SalesHistory.shopkeeper_id == shopkeeper_id).all()
    lifetime_revenue = sum(float(s.final_amount) for s in all_sales)
    lifetime_tx_count = len(all_sales)
    lifetime_items_sold = sum(
        sum(float(i.quantity) for i in s.items) if s.items else 1.0
        for s in all_sales
    )

    # Top selling products
    top_products_query = db.query(
        func.coalesce(SalesHistoryItem.item_name, Inventory.item_name, "General Item").label("product_name"),
        func.sum(SalesHistoryItem.quantity).label("total_qty"),
        func.sum(SalesHistoryItem.subtotal).label("total_rev")
    ).join(SalesHistory, SalesHistoryItem.sale_id == SalesHistory.sale_id
    ).outerjoin(Inventory, SalesHistoryItem.item_id == Inventory.item_id
    ).filter(SalesHistory.shopkeeper_id == shopkeeper_id
    ).group_by(func.coalesce(SalesHistoryItem.item_name, Inventory.item_name, "General Item")
    ).order_by(desc("total_qty")
    ).limit(6).all()

    top_products = [
        {
            "product_name": row[0],
            "total_quantity": float(row[1] or 0),
            "total_revenue": float(row[2] or 0)
        }
        for row in top_products_query
    ]

    return {
        "status": "SUCCESS",
        "shopkeeper_id": shopkeeper_id,
        "today_revenue": round(today_revenue, 2),
        "today_items_sold": round(today_items_sold, 1),
        "today_transactions": today_tx_count,
        "weekly_revenue": round(weekly_revenue, 2),
        "weekly_transactions": weekly_tx_count,
        "monthly_revenue": round(monthly_revenue, 2),
        "monthly_transactions": monthly_tx_count,
        "monthly_items_sold": round(monthly_items_sold, 1),
        "lifetime_revenue": round(lifetime_revenue, 2),
        "lifetime_transactions": lifetime_tx_count,
        "lifetime_items_sold": round(lifetime_items_sold, 1),
        "top_selling_products": top_products
    }


@app.get("/api/sales/product-history", tags=["Sales & Analytics"])
def get_product_sales_history(
    shopkeeper_id: int = Query(...),
    product_name: str = Query(..., min_length=1),
    db: Session = Depends(get_db)
):
    """
    Returns transaction-level sales history for a specific product item name.
    """
    search_term = f"%{product_name.strip()}%"
    items_query = db.query(SalesHistoryItem, SalesHistory).join(
        SalesHistory, SalesHistoryItem.sale_id == SalesHistory.sale_id
    ).filter(
        SalesHistory.shopkeeper_id == shopkeeper_id,
        or_(
            SalesHistoryItem.item_name.ilike(search_term),
            SalesHistory.items_summary.ilike(search_term)
        )
    ).order_by(SalesHistory.sale_date.desc()).all()

    history = []
    total_qty = 0.0
    total_rev = 0.0

    for shi, sh in items_query:
        qty = float(shi.quantity)
        sub = float(shi.subtotal)
        total_qty += qty
        total_rev += sub
        history.append({
            "sale_id": sh.sale_id,
            "id": sh.sale_id,
            "timestamp": sh.sale_date.isoformat(),
            "sale_date": sh.sale_date.strftime("%d %b %Y, %I:%M %p"),
            "item_name": shi.item_name or product_name,
            "quantity": qty,
            "unit_price": float(shi.unit_price),
            "total_price": sub,
            "subtotal": sub,
            "customer_name": sh.customer_name or "Walk-In Customer",
            "payment_mode": sh.payment_mode
        })

    return {
        "status": "SUCCESS",
        "shopkeeper_id": shopkeeper_id,
        "product_name": product_name,
        "total_quantity_sold": round(total_qty, 2),
        "total_revenue": round(total_rev, 2),
        "transactions_count": len(history),
        "history": history
    }


# ============================================================================
# 8. KHATA LEDGER MANAGEMENT (STRICT USER ISOLATION & SMART CREDIT CAPPING)
# ============================================================================

def check_credit_limit_status(current_balance: float, credit_limit: float, customer_name: str = "Customer"):
    """
    Evaluates customer's pending credit balance against their defined credit cap.
    Returns breach flags, warning messages, and demand cash advice.
    """
    limit = max(1.0, float(credit_limit or 2000.0))
    bal = float(current_balance)
    pct_used = round((bal / limit) * 100.0, 1)

    if bal >= limit:
        return {
            "credit_limit": limit,
            "current_balance": bal,
            "credit_limit_exceeded": True,
            "credit_limit_approaching": True,
            "credit_used_percent": pct_used,
            "alert_level": "CRITICAL",
            "demand_cash_required": True,
            "warning_message": f"🚨 CREDIT LIMIT EXCEEDED! {customer_name}'s pending balance is ₹{bal:.2f}, exceeding their capped limit of ₹{limit:.2f} ({pct_used}% used). DO NOT issue more credit. Demand cash payment immediately!",
            "badge_label": "Limit Exceeded",
            "badge_color": "crimson"
        }
    elif bal >= (limit * 0.85):
        return {
            "credit_limit": limit,
            "current_balance": bal,
            "credit_limit_exceeded": False,
            "credit_limit_approaching": True,
            "credit_used_percent": pct_used,
            "alert_level": "WARNING",
            "demand_cash_required": False,
            "warning_message": f"⚠️ APPROACHING CREDIT CAP: {customer_name}'s pending dues (₹{bal:.2f}) reached {pct_used}% of their ₹{limit:.2f} limit. Advise requesting cash payment for new items.",
            "badge_label": "Near Cap (85%+)",
            "badge_color": "amber"
        }
    else:
        return {
            "credit_limit": limit,
            "current_balance": bal,
            "credit_limit_exceeded": False,
            "credit_limit_approaching": False,
            "credit_used_percent": pct_used,
            "alert_level": "NORMAL",
            "demand_cash_required": False,
            "warning_message": None,
            "badge_label": "Healthy",
            "badge_color": "emerald"
        }


@app.get("/api/khata", tags=["Khata"])
def get_shopkeeper_khata(shopkeeper_id: int = Query(...), db: Session = Depends(get_db)):
    """Returns customer credit accounts strictly for this shopkeeper with credit limit tracking."""
    today = date.today()
    records = db.query(Khata).filter(Khata.shopkeeper_id == shopkeeper_id).order_by(Khata.khata_id.desc()).all()

    total_udhaar = sum(float(k.current_balance) for k in records)
    customers_list = []
    limit_breaches = 0
    limit_warnings = 0
    critical_alerts = []

    for k in records:
        cust_name = k.customer_name
        cust_phone = k.customer_phone
        if not cust_name and k.customer_id:
            c_user = db.query(User).filter(User.user_id == k.customer_id).first()
            if c_user:
                cust_name = c_user.full_name
                cust_phone = c_user.phone_number

        bal = float(k.current_balance)
        limit_val = float(k.credit_limit or 2000.0)
        is_overdue = (k.billing_reminder_date < today) if (k.billing_reminder_date and bal > 0) else False

        limit_status = check_credit_limit_status(bal, limit_val, cust_name or "Customer")
        if limit_status["credit_limit_exceeded"]:
            limit_breaches += 1
            critical_alerts.append({
                "khata_id": k.khata_id,
                "customer_name": cust_name or "Customer",
                "phone_number": cust_phone or "N/A",
                "guarantor_name": k.guarantor_name,
                "guarantor_phone": k.guarantor_phone,
                "balance": bal,
                "credit_limit": limit_val,
                "percent_used": limit_status["credit_used_percent"],
                "message": limit_status["warning_message"]
            })
        elif limit_status["credit_limit_approaching"]:
            limit_warnings += 1

        customers_list.append({
            "khata_id": k.khata_id,
            "customer_id": k.customer_id,
            "customer_name": cust_name or "Customer",
            "phone_number": cust_phone or "N/A",
            "guarantor_name": k.guarantor_name,
            "guarantor_phone": k.guarantor_phone,
            "current_balance": bal,
            "credit_limit": limit_val,
            "credit_limit_status": limit_status,
            "billing_reminder_date": k.billing_reminder_date.isoformat() if k.billing_reminder_date else None,
            "is_overdue": is_overdue,
            "reminder_status": k.reminder_status
        })

    return {
        "status": "SUCCESS",
        "shopkeeper_id": shopkeeper_id,
        "total_active_udhaar": total_udhaar,
        "customers_count": len(customers_list),
        "dues_count": sum(1 for c in customers_list if c["current_balance"] > 0),
        "credit_limit_breaches": limit_breaches,
        "credit_limit_warnings": limit_warnings,
        "critical_alerts": critical_alerts,
        "customers": customers_list
    }


@app.post("/api/khata", tags=["Khata"])
def create_khata_customer(payload: CreateKhataAccountRequest, db: Session = Depends(get_db)):
    """Creates a new customer credit ledger under the shopkeeper's account."""
    rem_date = None
    if payload.billing_reminder_date:
        try:
            rem_date = datetime.strptime(payload.billing_reminder_date, "%Y-%m-%d").date()
        except Exception:
            rem_date = date.today() + timedelta(days=15)
    else:
        rem_date = date.today() + timedelta(days=15)

    clean_phone = payload.customer_phone.strip().replace(" ", "").replace("+91", "") if payload.customer_phone else None
    if not clean_phone:
        clean_phone = f"91000{abs(hash(payload.customer_name)) % 100000:05d}"

    c_user = db.query(User).filter(User.phone_number == clean_phone).first()
    if not c_user:
        c_user = User(
            full_name=payload.customer_name.strip(),
            phone_number=clean_phone,
            email=f"{clean_phone}@vyaparhub.local",
            role="customer",
            is_active=True
        )
        db.add(c_user)
        db.commit()
        db.refresh(c_user)

    init_bal = Decimal(str(payload.initial_balance))
    credit_limit_dec = Decimal(str(payload.credit_limit))
    if init_bal > credit_limit_dec:
        raise HTTPException(
            status_code=400,
            detail=f"⚠️ Credit Limit Exceeded! Initial credit (₹{float(init_bal):.2f}) cannot exceed the permitted credit cap of ₹{float(credit_limit_dec):.2f}."
        )

    khata = Khata(
        shopkeeper_id=payload.shopkeeper_id,
        customer_id=c_user.user_id,
        customer_name=payload.customer_name.strip(),
        customer_phone=clean_phone,
        credit_limit=credit_limit_dec,
        current_balance=init_bal,
        billing_reminder_date=rem_date,
        guarantor_name=payload.guarantor_name.strip() if payload.guarantor_name else None,
        guarantor_phone=payload.guarantor_phone.strip() if payload.guarantor_phone else None
    )
    db.add(khata)
    db.commit()
    db.refresh(khata)

    if init_bal > 0:
        tx = KhataTransaction(
            khata_id=khata.khata_id,
            transaction_type="GIVE_CREDIT",
            amount=init_bal,
            balance_before=Decimal("0.00"),
            balance_after=init_bal,
            note="Initial Credit upon Registration"
        )
        db.add(tx)
        db.commit()

    return {
        "status": "SUCCESS",
        "message": f"Khata account created for {khata.customer_name}.",
        "khata_id": khata.khata_id,
        "current_balance": float(init_bal),
        "credit_limit": float(khata.credit_limit or 5000.0),
        "guarantor_name": khata.guarantor_name,
        "guarantor_phone": khata.guarantor_phone
    }


@app.post("/api/khata/{khata_id}/transaction", tags=["Khata"])
def record_khata_transaction(khata_id: int, payload: KhataTransactionRequest, db: Session = Depends(get_db)):
    khata = db.query(Khata).filter(Khata.khata_id == khata_id, Khata.shopkeeper_id == payload.shopkeeper_id).first()
    if not khata:
        raise HTTPException(status_code=404, detail="Khata account not found.")

    amt = Decimal(str(payload.amount))
    bal_before = khata.current_balance

    if payload.transaction_type == "GIVE_CREDIT":
        limit = float(khata.credit_limit or 2000.0)
        proposed_bal = float(bal_before + amt)

        if proposed_bal > limit and not payload.override_limit:
            excess = proposed_bal - limit
            raise HTTPException(
                status_code=400,
                detail=f"⚠️ Credit Limit Exceeded! Cannot add amount beyond the permitted credit cap of ₹{limit:.2f}. Current dues: ₹{float(bal_before):.2f}, Attempted credit: ₹{float(amt):.2f}, Proposed total: ₹{proposed_bal:.2f} (Excess: ₹{excess:.2f})."
            )
        bal_after = bal_before + amt
    else:
        bal_after = max(Decimal("0.00"), bal_before - amt)
        khata.last_payment_date = datetime.utcnow()

    khata.current_balance = bal_after
    note_text = payload.note
    if not note_text:
        if payload.transaction_type == "GIVE_CREDIT":
            note_text = "Credit added (Authorized Override)" if payload.override_limit else "Credit added"
        else:
            note_text = "Payment received"

    tx = KhataTransaction(
        khata_id=khata.khata_id,
        transaction_type=payload.transaction_type,
        amount=amt,
        balance_before=bal_before,
        balance_after=bal_after,
        note=note_text
    )
    db.add(tx)

    # Calculate credit limit capping & breach status
    limit_status = check_credit_limit_status(
        current_balance=float(bal_after),
        credit_limit=float(khata.credit_limit or 2000.0),
        customer_name=khata.customer_name or "Customer"
    )

    if payload.transaction_type == "GIVE_CREDIT" and limit_status["credit_limit_exceeded"] and khata.customer_id:
        alert_msg = Message(
            sender_id=payload.shopkeeper_id,
            receiver_id=khata.customer_id,
            shopkeeper_id=payload.shopkeeper_id,
            customer_id=khata.customer_id,
            sender_role="shopkeeper",
            content=f"⚠️ Credit Capping Alert: Your total pending dues have reached ₹{float(bal_after):.2f}, exceeding your store credit limit of ₹{float(khata.credit_limit):.2f}. Please clear dues via cash or UPI.",
            message_type="CREDIT_LIMIT_ALERT"
        )
        db.add(alert_msg)

    db.commit()
    db.refresh(khata)

    return {
        "status": "SUCCESS",
        "new_balance": float(bal_after),
        "credit_limit": float(khata.credit_limit),
        "customer_name": khata.customer_name,
        "customer_phone": khata.customer_phone,
        "guarantor_name": khata.guarantor_name,
        "guarantor_phone": khata.guarantor_phone,
        "transaction_id": tx.transaction_id,
        "credit_limit_status": limit_status,
        "credit_limit_exceeded": limit_status["credit_limit_exceeded"],
        "credit_limit_approaching": limit_status["credit_limit_approaching"],
        "demand_cash_required": limit_status["demand_cash_required"],
        "warning_message": limit_status["warning_message"]
    }


@app.put("/api/khata/{khata_id}/credit-limit", tags=["Khata"])
def update_khata_credit_limit(khata_id: int, payload: UpdateCreditLimitRequest, db: Session = Depends(get_db)):
    """Sets a personalized credit limit cap for a customer account."""
    khata = db.query(Khata).filter(Khata.khata_id == khata_id, Khata.shopkeeper_id == payload.shopkeeper_id).first()
    if not khata:
        raise HTTPException(status_code=404, detail="Khata account not found.")

    new_limit = max(100.0, float(payload.credit_limit))
    khata.credit_limit = Decimal(str(new_limit))
    db.commit()
    db.refresh(khata)

    limit_status = check_credit_limit_status(
        current_balance=float(khata.current_balance),
        credit_limit=float(khata.credit_limit),
        customer_name=khata.customer_name or "Customer"
    )

    return {
        "status": "SUCCESS",
        "khata_id": khata.khata_id,
        "customer_name": khata.customer_name,
        "credit_limit": float(khata.credit_limit),
        "current_balance": float(khata.current_balance),
        "credit_limit_status": limit_status,
        "message": f"Credit limit for {khata.customer_name} updated to ₹{new_limit:.2f}."
    }


@app.put("/api/khata/{khata_id}/guarantor", tags=["Khata"])
def update_khata_guarantor(khata_id: int, payload: UpdateGuarantorRequest, db: Session = Depends(get_db)):
    """Sets or updates family reference / guarantor details for a customer account."""
    khata = db.query(Khata).filter(Khata.khata_id == khata_id, Khata.shopkeeper_id == payload.shopkeeper_id).first()
    if not khata:
        raise HTTPException(status_code=404, detail="Khata account not found.")

    khata.guarantor_name = payload.guarantor_name.strip() if payload.guarantor_name else None
    khata.guarantor_phone = payload.guarantor_phone.strip() if payload.guarantor_phone else None
    db.commit()
    db.refresh(khata)

    return {
        "status": "SUCCESS",
        "message": f"Family reference / guarantor for {khata.customer_name} updated successfully.",
        "khata_id": khata.khata_id,
        "customer_name": khata.customer_name,
        "guarantor_name": khata.guarantor_name,
        "guarantor_phone": khata.guarantor_phone
    }


@app.get("/api/khata/customer/{customer_id}", tags=["Khata"])
def get_customer_khata_passbook(customer_id: int, db: Session = Depends(get_db)):
    """Returns a customer's individual credit dues and passbook across stores."""
    customer = db.query(User).filter(User.user_id == customer_id).first()
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found.")

    khatas = db.query(Khata).filter(
        (Khata.customer_id == customer_id) | (Khata.customer_phone == customer.phone_number)
    ).all()

    total_due = sum(float(k.current_balance) for k in khatas)
    passbook = []
    stores_breakdown = []
    for k in khatas:
        shop = db.query(User).filter(User.user_id == k.shopkeeper_id).first()
        shop_title = (shop.shop_name if shop and shop.shop_name else (shop.full_name if shop else "Kirana Store"))
        limit_val = float(k.credit_limit or 2000.0)
        bal_val = float(k.current_balance)
        used_pct = round((bal_val / limit_val) * 100, 1) if limit_val > 0 else 0
        stores_breakdown.append({
            "khata_id": k.khata_id,
            "shopkeeper_id": k.shopkeeper_id,
            "shop_name": shop_title,
            "current_balance": bal_val,
            "credit_limit": limit_val,
            "credit_used_percent": used_pct,
            "guarantor_name": k.guarantor_name,
            "guarantor_phone": k.guarantor_phone,
            "is_breached": bal_val >= limit_val,
            "is_near_cap": bal_val >= (limit_val * 0.85) and bal_val < limit_val
        })

        txs = db.query(KhataTransaction).filter(KhataTransaction.khata_id == k.khata_id).order_by(KhataTransaction.created_at.desc()).limit(5).all()
        for t in txs:
            passbook.append({
                "shop_name": shop_title,
                "transaction_type": t.transaction_type,
                "amount": float(t.amount),
                "note": t.note,
                "created_at": t.created_at.isoformat()
            })

    return {
        "customer_id": customer_id,
        "total_outstanding_dues": total_due,
        "accounts_count": len(khatas),
        "stores_breakdown": stores_breakdown,
        "recent_entries": passbook
    }


# ============================================================================
# 9. REAL-TIME ORDERS (STRICT USER ISOLATION & DYNAMIC INVENTORY DEDUCTION)
# ============================================================================

@app.post("/api/orders", tags=["Orders"])
def place_order(payload: PlaceOrderRequest, db: Session = Depends(get_db)):
    """Places an order routed specifically to the target shopkeeper with status PENDING."""
    import time
    import uuid
    order_id = f"VH-{int(time.time() * 1000) % 10000000}-{uuid.uuid4().hex[:4].upper()}"

    target_shop_id = payload.shopkeeper_id or payload.shop_id
    if not target_shop_id:
        raise HTTPException(status_code=400, detail="shopkeeper_id or shop_id is required")

    cust_id = payload.customer_id
    if not cust_id and payload.customer_phone:
        user_match = db.query(User).filter(User.phone_number == payload.customer_phone.strip()).first()
        if user_match:
            cust_id = user_match.user_id

    order = Order(
        order_id=order_id,
        shopkeeper_id=target_shop_id,
        customer_id=cust_id,
        customer_name=payload.customer_name.strip(),
        customer_phone=payload.customer_phone.strip(),
        delivery_address=payload.delivery_address or "Mandsaur, MP",
        total_amount=Decimal(str(payload.total_amount)),
        status="PENDING",
        payment_mode=payload.payment_mode or "UPI"
    )
    db.add(order)
    db.commit()

    items_summary_list = []
    items_detail_list = []
    for it in payload.items:
        sub = Decimal(str(it.price * it.qty))
        oi = OrderItem(
            order_id=order_id,
            item_id=it.item_id,
            name=it.name,
            price=Decimal(str(it.price)),
            qty=it.qty,
            subtotal=sub
        )
        db.add(oi)
        items_summary_list.append(f"{it.qty}x {it.name}")
        items_detail_list.append({"item_id": it.item_id, "name": it.name, "qty": it.qty, "price": it.price, "subtotal": float(sub)})

    db.commit()
    db.refresh(order)

    return {
        "status": "SUCCESS",
        "message": f"Order {order_id} placed successfully!",
        "order": {
            "order_id": order.order_id,
            "shopkeeper_id": order.shopkeeper_id,
            "customer_name": order.customer_name,
            "customer_phone": order.customer_phone,
            "delivery_address": order.delivery_address,
            "total_amount": float(order.total_amount),
            "items_summary": ", ".join(items_summary_list),
            "items": items_detail_list,
            "status": order.status,
            "created_at": order.created_at.isoformat()
        }
    }


@app.get("/api/orders/shopkeeper", tags=["Orders"])
def get_shopkeeper_orders(shopkeeper_id: int = Query(...), db: Session = Depends(get_db)):
    """Fetches all orders routed strictly to this shopkeeper with full line-item details."""
    orders = db.query(Order).filter(Order.shopkeeper_id == shopkeeper_id).order_by(Order.created_at.desc()).all()
    res = []
    for o in orders:
        items_list = [f"{i.qty}x {i.name}" for i in o.items]
        items_detail = [{"item_id": getattr(i, "item_id", None), "name": i.name, "qty": i.qty, "price": float(i.price), "subtotal": float(i.subtotal)} for i in o.items]
        res.append({
            "order_id": o.order_id,
            "customer_id": o.customer_id,
            "customer_name": o.customer_name,
            "customer_phone": o.customer_phone,
            "delivery_address": o.delivery_address,
            "total_amount": float(o.total_amount),
            "payment_mode": o.payment_mode,
            "status": o.status,
            "items_summary": ", ".join(items_list),
            "items": items_detail,
            "created_at": o.created_at.isoformat()
        })
    return {
        "shopkeeper_id": shopkeeper_id,
        "total_orders": len(res),
        "orders": res
    }


@app.get("/api/orders/pending", tags=["Orders"])
def get_pending_orders(shopkeeper_id: int = Query(...), db: Session = Depends(get_db)):
    """Long-poll / real-time feed of pending/received orders for a specific shopkeeper."""
    orders = db.query(Order).filter(
        Order.shopkeeper_id == shopkeeper_id,
        Order.status.in_(["RECEIVED", "PENDING"])
    ).order_by(Order.created_at.desc()).all()

    res = []
    for o in orders:
        items_list = [f"{i.qty}x {i.name}" for i in o.items]
        items_detail = [{"item_id": getattr(i, "item_id", None), "name": i.name, "qty": i.qty, "price": float(i.price), "subtotal": float(i.subtotal)} for i in o.items]
        res.append({
            "order_id": o.order_id,
            "customer_id": o.customer_id,
            "customer_name": o.customer_name,
            "customer_phone": o.customer_phone,
            "delivery_address": o.delivery_address,
            "total_amount": float(o.total_amount),
            "payment_mode": o.payment_mode,
            "status": o.status,
            "items_summary": ", ".join(items_list),
            "items": items_detail,
            "created_at": o.created_at.isoformat()
        })
    return {
        "shopkeeper_id": shopkeeper_id,
        "pending_count": len(res),
        "orders": res
    }


@app.put("/api/orders/{order_id}/status", tags=["Orders"])
def update_order_status(order_id: str, payload: OrderStatusUpdateRequest, db: Session = Depends(get_db)):
    """
    Updates order status (ACCEPTED, REJECTED, PACKED, OUT_FOR_DELIVERY, COMPLETED).
    When an order is ACCEPTED, dynamically deducts item quantities from the shopkeeper's active inventory.
    """
    order = db.query(Order).filter(Order.order_id == order_id).first()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found.")

    old_status = (order.status or "").upper()
    new_status = payload.status.upper()
    order.status = new_status

    # Dynamic inventory stock deduction when order is accepted
    if old_status in ["PENDING", "RECEIVED"] and new_status in ["ACCEPTED", "PACKED"]:
        for oi in order.items:
            inv_item = None
            if getattr(oi, "item_id", None):
                inv_item = db.query(Inventory).filter(
                    Inventory.shopkeeper_id == order.shopkeeper_id,
                    Inventory.item_id == oi.item_id,
                    Inventory.is_active == True
                ).first()
            if not inv_item:
                inv_item = db.query(Inventory).filter(
                    Inventory.shopkeeper_id == order.shopkeeper_id,
                    Inventory.is_active == True,
                    Inventory.item_name.ilike(oi.name)
                ).order_by(Inventory.item_id.desc()).first()
            if inv_item:
                deduct_qty = Decimal(str(oi.qty))
                inv_item.stock_quantity = max(Decimal("0.00"), inv_item.stock_quantity - deduct_qty)

    # Dispatch automated status message to customer
    if order.customer_id:
        status_descriptions = {
            "ACCEPTED": f"✅ Order #{order.order_id} Accepted! The merchant has started preparing your fresh groceries.",
            "REJECTED": f"❌ Order #{order.order_id} could not be accepted by the store at this moment.",
            "PACKED": f"📦 Order #{order.order_id} is packed and ready for dispatch.",
            "OUT_FOR_DELIVERY": f"🚚 Order #{order.order_id} is out for delivery! Arriving shortly.",
            "COMPLETED": f"🏠 Order #{order.order_id} has been delivered. Thank you for supporting local Kirana!",
            "DELIVERED": f"🏠 Order #{order.order_id} has been delivered. Thank you for supporting local Kirana!"
        }
        notif_text = status_descriptions.get(new_status, f"Order #{order.order_id} status updated to {new_status}.")
        notif_msg = Message(
            sender_id=order.shopkeeper_id,
            receiver_id=order.customer_id,
            shopkeeper_id=order.shopkeeper_id,
            customer_id=order.customer_id,
            sender_role="shopkeeper",
            content=notif_text,
            message_type="ORDER_STATUS_UPDATE",
            is_read=False
        )
    # Record completed customer order into sales_history
    if old_status not in ["COMPLETED", "DELIVERED"] and new_status in ["COMPLETED", "DELIVERED"]:
        items_desc = ", ".join([f"{oi.qty}x {oi.name}" for oi in order.items])
        order_sale = SalesHistory(
            shopkeeper_id=order.shopkeeper_id,
            customer_id=order.customer_id,
            customer_name=order.customer_name or "Online Order Customer",
            items_summary=items_desc,
            total_amount=order.total_amount,
            discount_amount=Decimal("0.00"),
            final_amount=order.total_amount,
            payment_mode=order.payment_mode or "UPI",
            sale_date=datetime.utcnow()
        )
        db.add(order_sale)
        db.flush()

        for oi in order.items:
            shi = SalesHistoryItem(
                sale_id=order_sale.sale_id,
                item_id=getattr(oi, "item_id", None),
                item_name=oi.name,
                quantity=Decimal(str(oi.qty)),
                unit_price=Decimal(str(oi.price)),
                purchase_price=Decimal("0.00"),
                subtotal=Decimal(str(oi.subtotal))
            )
            db.add(shi)

    db.commit()
    db.refresh(order)
    return {"status": "SUCCESS", "order_id": order.order_id, "new_status": order.status}


@app.get("/api/customer/orders", tags=["Orders", "Customer"])
def get_customer_orders_history(
    customer_id: Optional[int] = Query(None),
    phone: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    db: Session = Depends(get_db)
):
    """
    Returns the customer's complete order history along with the corresponding
    Shop Name, items ordered, total price, timestamp, and live status.
    """
    query = db.query(Order)
    if customer_id:
        query = query.filter(Order.customer_id == customer_id)
    elif phone:
        query = query.filter(Order.customer_phone == phone.strip())
    else:
        return {"status": "SUCCESS", "customer_id": None, "total_orders": 0, "total_spent": 0.0, "orders": []}

    if status and status.strip() and status.strip().upper() != "ALL":
        query = query.filter(Order.status == status.strip().upper())

    orders = query.order_by(Order.created_at.desc()).all()
    res = []
    total_spent = 0.0

    for o in orders:
        sk = db.query(User).filter(User.user_id == o.shopkeeper_id).first()
        shop_title = (sk.shop_name if sk and sk.shop_name else (sk.full_name if sk else "Local Kirana Store"))
        items_list = [f"{i.qty}x {i.name}" for i in o.items]
        items_detail = [
            {
                "item_id": getattr(i, "item_id", None),
                "name": i.name,
                "item_name": i.name,
                "qty": i.qty,
                "quantity": i.qty,
                "price": float(i.price),
                "unit_price": float(i.price),
                "subtotal": float(i.subtotal)
            }
            for i in o.items
        ]
        price_val = float(o.total_amount)
        total_spent += price_val
        res.append({
            "id": o.order_id,
            "order_id": o.order_id,
            "shop_id": o.shopkeeper_id,
            "shopkeeper_id": o.shopkeeper_id,
            "customer_id": o.customer_id,
            "customer_name": o.customer_name,
            "customer_phone": o.customer_phone,
            "shop_name": shop_title,
            "shop_category": (sk.shop_category if sk and sk.shop_category else "Kirana & General Store"),
            "shop_phone": sk.phone_number if sk else "",
            "shop_address": sk.address if sk else "Mandsaur, MP",
            "total_price": price_val,
            "total_amount": price_val,
            "payment_mode": o.payment_mode,
            "status": o.status,
            "items_summary": ", ".join(items_list),
            "items": items_detail,
            "timestamp": o.created_at.isoformat(),
            "created_at": o.created_at.isoformat(),
            "formatted_date": o.created_at.strftime("%d %b %Y, %I:%M %p"),
            "delivery_address": o.delivery_address or "Customer Address"
        })

    return {
        "status": "SUCCESS",
        "customer_id": customer_id,
        "total_orders": len(res),
        "total_spent": round(total_spent, 2),
        "orders": res
    }


@app.get("/api/orders/customer", tags=["Orders"])
def get_customer_orders(customer_id: Optional[int] = None, phone: Optional[str] = None, status: Optional[str] = None, db: Session = Depends(get_db)):
    """Fetches real orders placed by this specific customer with timeline tracking (alias for /api/customer/orders)."""
    return get_customer_orders_history(customer_id=customer_id, phone=phone, status=status, db=db)


# ============================================================================
# 9.5 PURE-PYTHON SVG QR CODE ENGINE & DIGITAL CATALOGUE SHARING
# ============================================================================

GF_EXP = [0] * 512
GF_LOG = [0] * 256

def _init_gf():
    x = 1
    for i in range(255):
        GF_EXP[i] = x
        GF_EXP[i + 255] = x
        GF_LOG[x] = i
        x <<= 1
        if x & 0x100:
            x ^= 0x11d
    GF_LOG[0] = 0

_init_gf()

def gf_mul(x, y):
    if x == 0 or y == 0:
        return 0
    return GF_EXP[GF_LOG[x] + GF_LOG[y]]

def rs_generator_poly(degree):
    poly = [1]
    for i in range(degree):
        next_poly = [0] * (len(poly) + 1)
        root = GF_EXP[i]
        for j, c in enumerate(poly):
            next_poly[j] ^= gf_mul(c, root)
            next_poly[j + 1] ^= c
        poly = next_poly
    return poly

def rs_encode(data, num_ec):
    gen = rs_generator_poly(num_ec)
    msg = list(data) + [0] * num_ec
    for i in range(len(data)):
        lead = msg[i]
        if lead != 0:
            for j in range(len(gen)):
                msg[i + j] ^= gf_mul(gen[j], lead)
    return msg[-num_ec:]

VERSIONS_INFO = {
    1: {"size": 21, "data_cw": 19, "ec_cw": 7, "align": []},
    2: {"size": 25, "data_cw": 34, "ec_cw": 10, "align": [6, 18]},
    3: {"size": 29, "data_cw": 55, "ec_cw": 15, "align": [6, 22]},
}

def generate_qr_matrix(text: str):
    data_bytes = text.encode("utf-8")
    length = len(data_bytes)
    v_num = 1
    if length > 17:
        v_num = 2
    if length > 32:
        v_num = 3

    v = VERSIONS_INFO[v_num]
    size = v["size"]
    total_data = v["data_cw"]
    ec_count = v["ec_cw"]

    bits = "0100" + f"{min(length, total_data - 2):08b}"
    for b in data_bytes[:total_data - 2]:
        bits += f"{b:08b}"
    bits += "0000"
    if len(bits) % 8 != 0:
        bits += "0" * (8 - (len(bits) % 8))

    codewords = [int(bits[i:i+8], 2) for i in range(0, len(bits), 8)]
    pad_bytes = [0xEC, 0x11]
    p_idx = 0
    while len(codewords) < total_data:
        codewords.append(pad_bytes[p_idx % 2])
        p_idx += 1

    codewords = codewords[:total_data]
    ec_codewords = rs_encode(codewords, ec_count)
    all_codewords = codewords + ec_codewords

    grid = [[None] * size for _ in range(size)]
    reserved = [[False] * size for _ in range(size)]

    def place_finder(r0, c0):
        for r in range(7):
            for c in range(7):
                is_border = (r in (0, 6) or c in (0, 6))
                is_inner = (2 <= r <= 4 and 2 <= c <= 4)
                grid[r0 + r][c0 + c] = is_border or is_inner
                reserved[r0 + r][c0 + c] = True
        for r in range(-1, 8):
            for c in range(-1, 8):
                rr, cc = r0 + r, c0 + c
                if 0 <= rr < size and 0 <= cc < size:
                    if not reserved[rr][cc]:
                        grid[rr][cc] = False
                        reserved[rr][cc] = True

    place_finder(0, 0)
    place_finder(0, size - 7)
    place_finder(size - 7, 0)

    for i in range(8, size - 8):
        grid[6][i] = (i % 2 == 0)
        reserved[6][i] = True
        grid[i][6] = (i % 2 == 0)
        reserved[i][6] = True

    if v["align"]:
        align_pos = v["align"]
        for ar in align_pos:
            for ac in align_pos:
                if (ar < 9 and ac < 9) or (ar < 9 and ac > size - 9) or (ar > size - 9 and ac < 9):
                    continue
                for dr in range(-2, 3):
                    for dc in range(-2, 3):
                        rr, cc = ar + dr, ac + dc
                        if not reserved[rr][cc]:
                            is_border = abs(dr) == 2 or abs(dc) == 2
                            is_center = (dr == 0 and dc == 0)
                            grid[rr][cc] = is_border or is_center
                            reserved[rr][cc] = True

    grid[4 * v_num + 9][8] = True
    reserved[4 * v_num + 9][8] = True

    for i in range(9):
        if not reserved[8][i]: reserved[8][i] = True
        if not reserved[i][8]: reserved[i][8] = True
    for i in range(size - 8, size):
        if not reserved[8][i]: reserved[8][i] = True
        if not reserved[i][8]: reserved[i][8] = True

    format_bits = "111011111000100"
    for i in range(6):
        grid[8][i] = (format_bits[i] == '1')
    grid[8][7] = (format_bits[6] == '1')
    grid[8][8] = (format_bits[7] == '1')
    grid[7][8] = (format_bits[8] == '1')
    for i in range(9, 15):
        grid[14 - i][8] = (format_bits[i] == '1')

    for i in range(8):
        grid[size - 1 - i][8] = (format_bits[i] == '1')
    for i in range(8, 15):
        grid[8][size - 15 + i] = (format_bits[i] == '1')

    all_bits = ""
    for cw in all_codewords:
        all_bits += f"{cw:08b}"
    bit_idx = 0

    right = size - 1
    up = True
    while right > 0:
        if right == 6:
            right -= 1
        rows = range(size - 1, -1, -1) if up else range(size)
        for r in rows:
            for c in (right, right - 1):
                if not reserved[r][c]:
                    b = False
                    if bit_idx < len(all_bits):
                        b = (all_bits[bit_idx] == '1')
                        bit_idx += 1
                    if (r + c) % 2 == 0:
                        b = not b
                    grid[r][c] = b
        right -= 2
        up = not up

    return grid

def generate_qr_svg(text: str, fill_color: str = "#001F5B", bg_color: str = "#FAF7EE", size_px: int = 260) -> str:
    matrix = generate_qr_matrix(text)
    n = len(matrix)
    cell_size = size_px / (n + 4)
    rects = []
    for r in range(n):
        for c in range(n):
            if matrix[r][c]:
                x = (c + 2) * cell_size
                y = (r + 2) * cell_size
                rects.append(f'<rect x="{x:.2f}" y="{y:.2f}" width="{cell_size:.2f}" height="{cell_size:.2f}" fill="{fill_color}" />')
    return f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {size_px} {size_px}" width="{size_px}" height="{size_px}">
  <rect width="{size_px}" height="{size_px}" fill="{bg_color}" rx="16" />
  {''.join(rects)}
</svg>'''


@app.get("/api/shops/{shopkeeper_id}/share-link", tags=["Marketplace"])
def get_shop_share_link(shopkeeper_id: int, db: Session = Depends(get_db)):
    """Generates a shareable store catalogue link, WhatsApp promo text, and QR code route."""
    sk = db.query(User).filter(User.user_id == shopkeeper_id, User.role == "shopkeeper").first()
    if not sk:
        raise HTTPException(status_code=404, detail="Shopkeeper not found.")

    shop_title = sk.shop_name or f"{sk.full_name}'s Kirana"
    base_url = "http://localhost:8000"
    share_url = f"{base_url}/#shop={sk.user_id}"
    catalog_api_url = f"{base_url}/api/customer/catalog?shopkeeper_id={sk.user_id}"
    qr_url = f"/api/shops/{sk.user_id}/qr"

    wa_msg = f"Namaste! Order fresh groceries directly from {shop_title}. View our digital catalogue and order here: {share_url}"
    whatsapp_url = f"https://api.whatsapp.com/send?text={urllib.parse.quote(wa_msg)}"

    return {
        "status": "SUCCESS",
        "shopkeeper_id": sk.user_id,
        "shop_name": shop_title,
        "owner_name": sk.full_name,
        "category": sk.shop_category or "General Kirana",
        "address": sk.address or "Mandsaur, Madhya Pradesh",
        "share_url": share_url,
        "catalog_api_url": catalog_api_url,
        "whatsapp_url": whatsapp_url,
        "whatsapp_text": wa_msg,
        "qr_url": qr_url
    }


@app.get("/api/shops/{shopkeeper_id}/qr", tags=["Marketplace"])
def get_shop_qr_code(shopkeeper_id: int, db: Session = Depends(get_db)):
    """Returns a standalone high-resolution SVG QR code for the shopkeeper's catalogue."""
    sk = db.query(User).filter(User.user_id == shopkeeper_id, User.role == "shopkeeper").first()
    if not sk:
        raise HTTPException(status_code=404, detail="Shopkeeper not found.")

    url_to_encode = f"http://localhost:8000/#shop={sk.user_id}"
    svg_content = generate_qr_svg(url_to_encode, fill_color="#001F5B", bg_color="#FAF7EE", size_px=280)
    return Response(content=svg_content, media_type="image/svg+xml")


# ============================================================================
# 10. CUSTOMER MARKETPLACE (DISCOVER SHOPS & REAL PRODUCTS)
# ============================================================================

@app.get("/api/shops", tags=["Marketplace"])
def get_registered_shops(db: Session = Depends(get_db)):
    """Returns registered Kirana merchants so customers can shop locally."""
    shopkeepers = db.query(User).filter(User.role == "shopkeeper", User.is_active == True).all()
    shops_data = []
    for sk in shopkeepers:
        item_count = db.query(Inventory).filter(
            Inventory.shopkeeper_id == sk.user_id,
            Inventory.is_active == True,
            Inventory.stock_quantity > 0
        ).count()
        shops_data.append({
            "shopkeeper_id": sk.user_id,
            "shop_name": sk.shop_name or f"{sk.full_name}'s Kirana Store",
            "owner_name": sk.full_name,
            "phone_number": sk.phone_number,
            "address": sk.address or "Mandsaur, Madhya Pradesh",
            "avatar_url": sk.avatar_url,
            "total_products": item_count,
            "rating": 4.9,
            "delivery_time": "15-20 mins"
        })
    return {"shops": shops_data}


@app.get("/api/customer/catalog", tags=["Marketplace"])
def get_customer_catalog(shopkeeper_id: Optional[int] = None, db: Session = Depends(get_db)):
    """Returns available products across shops or for a selected shop."""
    query = db.query(Inventory, User).join(User, Inventory.shopkeeper_id == User.user_id).filter(
        User.role == "shopkeeper",
        User.is_active == True,
        User.shop_name.isnot(None),
        func.trim(User.shop_name) != "",
        Inventory.is_active == True,
        Inventory.stock_quantity > 0
    )
    if shopkeeper_id:
        query = query.filter(Inventory.shopkeeper_id == shopkeeper_id)

    items = query.all()
    catalog = []
    for inv, sk in items:
        catalog.append({
            "item_id": inv.item_id,
            "shopkeeper_id": sk.user_id,
            "shop_name": (sk.shop_name or "").strip() or sk.full_name,
            "name": inv.item_name,
            "category": inv.category,
            "price": float(inv.current_selling_price),
            "original_price": float(inv.recommended_selling_price or inv.current_selling_price),
            "unit": inv.unit,
            "stock_quantity": float(inv.stock_quantity),
            "image_url": inv.image_url,
            "badge": "Fresh Daily" if inv.is_daily_need else "In Stock"
        })
    return {"catalog": catalog}


@app.get("/api/customer/flash-deals", tags=["Marketplace"])
def get_customer_flash_deals(db: Session = Depends(get_db)):
    """Returns active expiring items liquidation deals across registered stores."""
    today = date.today()
    expiring = db.query(Inventory, User).join(User, Inventory.shopkeeper_id == User.user_id).filter(
        User.role == "shopkeeper",
        User.is_active == True,
        User.shop_name.isnot(None),
        func.trim(User.shop_name) != "",
        Inventory.is_active == True,
        Inventory.expiry_date != None,
        Inventory.stock_quantity > 0
    ).all()

    flash_deals = []
    for inv, sk in expiring:
        days_left = (inv.expiry_date - today).days
        if days_left > 10:
            continue

        if days_left <= 2:
            disc_pct = 60.0
        elif days_left <= 5:
            disc_pct = 40.0
        else:
            disc_pct = 20.0

        orig_p = float(inv.current_selling_price)
        flash_p = round(orig_p * (1.0 - (disc_pct / 100.0)), 2)

        flash_deals.append({
            "item_id": inv.item_id,
            "shopkeeper_id": sk.user_id,
            "shop_name": (sk.shop_name or "").strip() or sk.full_name,
            "name": inv.item_name,
            "days_until_expiry": days_left,
            "original_price": orig_p,
            "flash_price": flash_p,
            "discount_pct": disc_pct,
            "unit": inv.unit,
            "stock_quantity": float(inv.stock_quantity)
        })

    flash_deals.sort(key=lambda x: x["days_until_expiry"])
    return {"flash_deals": flash_deals}


# ============================================================================
# 11. AI RECOMMENDATIONS HUB (ISOLATED TO SHOPKEEPER)
# ============================================================================

@app.post("/api/recommendations/smart-pricing", tags=["AI Recommendations"])
def smart_pricing_engine(payload: SmartPricingRequest, db: Session = Depends(get_db)):
    query = db.query(Inventory).filter(
        Inventory.shopkeeper_id == payload.shopkeeper_id,
        Inventory.is_active == True
    )
    if payload.item_id:
        query = query.filter(Inventory.item_id == payload.item_id)
    if payload.category:
        query = query.filter(Inventory.category.ilike(payload.category.strip()))

    items = query.all()
    if not items:
        return {"status": "EMPTY", "items_analyzed": 0, "recommendations": []}

    inflation_factor = 1.0 + (payload.wholesale_inflation_rate_pct / 100.0)
    pricing_recommendations = []

    for item in items:
        current_cost = float(item.purchase_price)
        current_sell = float(item.current_selling_price)
        adjusted_cost = current_cost * inflation_factor
        margin_decimal = payload.target_margin_pct / 100.0
        suggested_price = round(adjusted_cost / (1.0 - margin_decimal), 2)
        price_diff = round(suggested_price - current_sell, 2)
        projected_margin_pct = round(((suggested_price - adjusted_cost) / suggested_price) * 100.0, 1)

        recommendation = {
            "item_id": item.item_id,
            "item_name": item.item_name,
            "category": item.category,
            "current_purchase_cost": current_cost,
            "inflation_adjusted_cost": round(adjusted_cost, 2),
            "current_selling_price": current_sell,
            "suggested_smart_price": suggested_price,
            "price_adjustment": price_diff,
            "projected_gross_margin_pct": projected_margin_pct
        }
        if payload.apply_to_inventory:
            item.recommended_selling_price = Decimal(str(suggested_price))
            item.current_selling_price = Decimal(str(suggested_price))

        pricing_recommendations.append(recommendation)

    if payload.apply_to_inventory:
        db.commit()

    return {
        "status": "PRICING_COMPUTED",
        "applied_to_database": payload.apply_to_inventory,
        "items_analyzed": len(pricing_recommendations),
        "recommendations": pricing_recommendations
    }


@app.get("/api/recommendations/best-stock-to-buy", tags=["AI Recommendations"])
def get_best_stock_to_buy(
    shopkeeper_id: int = Query(...),
    current_season: str = Query("All-Season"),
    db: Session = Depends(get_db)
):
    inventory_items = db.query(Inventory).filter(
        Inventory.shopkeeper_id == shopkeeper_id,
        Inventory.is_active == True
    ).all()

    if not inventory_items:
        return {
            "season_context": current_season,
            "items_recommended_for_purchase": 0,
            "total_estimated_capital_required": 0.0,
            "recommendations": []
        }

    recommendations = []
    for item in inventory_items:
        stock_val = float(item.stock_quantity)
        thresh = float(item.low_stock_threshold)
        is_in_season = (item.seasonal_category == current_season) or (item.seasonal_category == "All-Season")

        if stock_val <= thresh:
            priority = "URGENT_RESTOCK_NOW"
            suggested_buy = max(25.0, thresh * 3.0)
        elif is_in_season and stock_val < 20.0:
            priority = "SEASONAL_UPCOMING_SURGE"
            suggested_buy = 20.0
        else:
            continue

        cost = round(suggested_buy * float(item.purchase_price), 2)
        recommendations.append({
            "item_id": item.item_id,
            "item_name": item.item_name,
            "category": item.category,
            "current_stock": stock_val,
            "unit": item.unit,
            "restock_priority": priority,
            "recommended_buy_quantity": suggested_buy,
            "estimated_reorder_cost": cost,
            "order_reason": "Low stock threshold alert." if priority == "URGENT_RESTOCK_NOW" else "Seasonal demand forecast."
        })

    return {
        "season_context": current_season,
        "items_recommended_for_purchase": len(recommendations),
        "total_estimated_capital_required": sum(x["estimated_reorder_cost"] for x in recommendations),
        "recommendations": recommendations
    }



# ============================================================================
# 12. DEDICATED CUSTOMER WORKFLOW: NEARBY SHOPS, FEED, KHATA & MESSAGING
# ============================================================================

@app.get("/api/customer/nearby-shops", tags=["Marketplace"])
def get_nearby_shops(
    customer_id: Optional[int] = None,
    address: Optional[str] = None,
    category: Optional[str] = None,
    query: Optional[str] = None,
    db: Session = Depends(get_db)
):
    """
    Returns verified Kirana stores with dynamic proximity and locality matching
    based on the customer's saved profile address and optional category filter.
    """
    cust = None
    if customer_id:
        cust = db.query(User).filter(User.user_id == customer_id).first()

    cust_addr = (address or (cust.address if cust else "") or "").lower()

    DUMMY_TEMPLATE_SHOPS = {
        "sharma kirana & general store",
        "dummy store",
        "sample store",
        "template kirana",
        "mock store",
        "test store",
    }

    # Query exclusively active shopkeeper accounts with a non-empty registered shop_name
    shopkeepers = db.query(User).filter(
        User.role == "shopkeeper",
        User.is_active == True,
        User.shop_name.isnot(None),
        func.trim(User.shop_name) != ""
    ).all()

    # Exclude dummy/mock template shops
    shopkeepers = [
        sk for sk in shopkeepers
        if (sk.shop_name or "").strip().lower() not in DUMMY_TEMPLATE_SHOPS
    ]
    shops = []

    for idx, sk in enumerate(shopkeepers):
        # Category filter check
        if category and category.strip() and category.strip().lower() != "all":
            cat_term = category.strip().lower()
            sk_cat = (sk.shop_category or "General Kirana").lower()
            # Also check if any active inventory matches this category
            has_cat_item = db.query(Inventory).filter(
                Inventory.shopkeeper_id == sk.user_id,
                Inventory.is_active == True,
                Inventory.category.ilike(f"%{cat_term}%")
            ).first() is not None

            if cat_term not in sk_cat and not has_cat_item:
                continue

        item_count = db.query(Inventory).filter(
            Inventory.shopkeeper_id == sk.user_id,
            Inventory.is_active == True,
            Inventory.stock_quantity > 0
        ).count()

        deals_count = db.query(Inventory).filter(
            Inventory.shopkeeper_id == sk.user_id,
            Inventory.is_active == True,
            Inventory.expiry_date != None,
            Inventory.stock_quantity > 0
        ).count()

        sk_addr = (sk.address or "Main Market, Mandsaur").lower()

        is_same_area = False
        for locality in ["civil lines", "mandi", "station", "ramganj", "nai abadi", "gandhi nagar", "bus stand", "bada bazar"]:
            if locality in cust_addr and locality in sk_addr:
                is_same_area = True
                break

        if is_same_area:
            dist_km = round(0.2 + (idx * 0.15), 1)
            dist_label = f"{dist_km} km away • Your Neighborhood"
        else:
            dist_km = round(0.5 + (idx * 0.4), 1)
            dist_label = f"{dist_km} km away"

        if query and query.strip():
            q_term = query.strip().lower()
            if q_term not in (sk.shop_name or "").lower() and q_term not in sk.full_name.lower() and q_term not in sk_addr and q_term not in (sk.shop_category or "").lower():
                continue

        shops.append({
            "shopkeeper_id": sk.user_id,
            "shop_name": (sk.shop_name or "").strip(),
            "owner_name": sk.full_name,
            "shop_category": sk.shop_category or "General Kirana",
            "phone_number": sk.phone_number,
            "address": sk.address or "Mandsaur, Madhya Pradesh",
            "avatar_url": sk.avatar_url,
            "latitude": float(sk.latitude) if sk.latitude is not None else None,
            "longitude": float(sk.longitude) if sk.longitude is not None else None,
            "distance_km": dist_km,
            "distance_label": dist_label,
            "total_products": item_count,
            "active_deals_count": deals_count,
            "delivery_time": "10-25 mins",
            "rating": 4.9
        })

    shops.sort(key=lambda s: s["distance_km"])
    return {"shops": shops, "customer_address": cust_addr or "Mandsaur"}


@app.get("/api/customer/feed", tags=["Marketplace"])
def get_customer_feed(customer_id: Optional[int] = None, db: Session = Depends(get_db)):
    """
    Social-media-style feed showing latest arrivals, seasonal highlights,
    and dynamic decay flash sales from nearby neighborhood stores.
    """
    today = date.today()
    feed_items = []

    # 1. Flash Deals from all stores
    flash_prods = db.query(Inventory, User).join(User, Inventory.shopkeeper_id == User.user_id).filter(
        User.role == "shopkeeper",
        User.is_active == True,
        User.shop_name.isnot(None),
        func.trim(User.shop_name) != "",
        Inventory.is_active == True,
        Inventory.expiry_date != None,
        Inventory.stock_quantity > 0
    ).all()

    for inv, sk in flash_prods:
        days_left = (inv.expiry_date - today).days
        if 0 <= days_left <= 7:
            disc_pct = 50.0 if days_left <= 2 else (35.0 if days_left <= 4 else 20.0)
            orig_p = float(inv.current_selling_price)
            flash_p = round(orig_p * (1.0 - (disc_pct / 100.0)), 2)
            feed_items.append({
                "post_id": f"flash_{inv.item_id}",
                "type": "FLASH_SALE",
                "badge": f"⚡ FLASH SALE • {int(disc_pct)}% OFF",
                "shopkeeper_id": sk.user_id,
                "shop_name": (sk.shop_name or "").strip() or sk.full_name,
                "shop_address": sk.address or "Mandsaur",
                "shop_avatar": sk.avatar_url,
                "item_id": inv.item_id,
                "item_name": inv.item_name,
                "category": inv.category,
                "unit": inv.unit,
                "price": flash_p,
                "original_price": orig_p,
                "discount_pct": disc_pct,
                "stock_quantity": float(inv.stock_quantity),
                "expiry_days_left": days_left,
                "caption": f"⚡ Dynamic Decay Alert: {inv.item_name} expiring in {days_left} days. Grab this bargain before stock runs out!",
                "created_at": inv.created_at.isoformat() if inv.created_at else None
            })

    # 2. New Arrivals (latest 12 items)
    new_prods = db.query(Inventory, User).join(User, Inventory.shopkeeper_id == User.user_id).filter(
        User.role == "shopkeeper",
        User.is_active == True,
        User.shop_name.isnot(None),
        func.trim(User.shop_name) != "",
        Inventory.is_active == True,
        Inventory.stock_quantity > 0
    ).order_by(desc(Inventory.created_at)).limit(12).all()

    for inv, sk in new_prods:
        feed_items.append({
            "post_id": f"new_{inv.item_id}",
            "type": "NEW_ARRIVAL",
            "badge": "✨ FRESH ARRIVAL",
            "shopkeeper_id": sk.user_id,
            "shop_name": (sk.shop_name or "").strip() or sk.full_name,
            "shop_address": sk.address or "Mandsaur",
            "shop_avatar": sk.avatar_url,
            "item_id": inv.item_id,
            "item_name": inv.item_name,
            "category": inv.category,
            "unit": inv.unit,
            "price": float(inv.current_selling_price),
            "original_price": float(inv.recommended_selling_price or inv.current_selling_price),
            "discount_pct": 0,
            "stock_quantity": float(inv.stock_quantity),
            "expiry_days_left": None,
            "caption": f"Fresh stock alert! {inv.item_name} now in stock at {sk.shop_name or sk.full_name}.",
            "created_at": inv.created_at.isoformat() if inv.created_at else None
        })

    return {"feed": feed_items}


@app.get("/api/customer/khata-dues", tags=["Khata"])
def get_customer_khata_dues(
    customer_id: Optional[int] = None,
    phone: Optional[str] = None,
    db: Session = Depends(get_db)
):
    """
    Returns customer's outstanding balance / udhaari across all Kirana stores,
    per-shop breakdown, and complete transaction passbook.
    """
    cust = None
    if customer_id:
        cust = db.query(User).filter(User.user_id == customer_id).first()
    clean_phone = (phone or (cust.phone_number if cust else "") or "").strip().replace(" ", "").replace("+91", "")
    cust_name = cust.full_name if cust else ""

    query = db.query(Khata).join(User, Khata.shopkeeper_id == User.user_id)
    if customer_id:
        query = query.filter((Khata.customer_id == customer_id) | (Khata.customer_phone == clean_phone) | (Khata.customer_name.ilike(f"%{cust_name}%")))
    elif clean_phone:
        query = query.filter(Khata.customer_phone == clean_phone)
    else:
        return {"total_due": 0.0, "stores_count": 0, "stores": [], "transactions": []}

    khata_records = query.all()
    stores_list = []
    total_due = Decimal("0.00")
    all_txs = []

    for k in khata_records:
        sk = db.query(User).filter(User.user_id == k.shopkeeper_id).first()
        bal = k.current_balance
        total_due += bal

        stores_list.append({
            "khata_id": k.khata_id,
            "shopkeeper_id": k.shopkeeper_id,
            "shop_name": sk.shop_name if sk and sk.shop_name else (sk.full_name if sk else "Kirana Store"),
            "owner_name": sk.full_name if sk else "Merchant",
            "phone_number": sk.phone_number if sk else "",
            "address": sk.address if sk else "Mandsaur",
            "avatar_url": sk.avatar_url if sk else None,
            "current_balance": float(bal),
            "billing_reminder_date": k.billing_reminder_date.isoformat() if k.billing_reminder_date else None,
            "reminder_status": k.reminder_status,
            "last_payment_date": k.last_payment_date.isoformat() if k.last_payment_date else None
        })

        for tx in k.transactions:
            all_txs.append({
                "transaction_id": tx.transaction_id,
                "khata_id": k.khata_id,
                "shop_name": sk.shop_name if sk and sk.shop_name else (sk.full_name if sk else "Kirana Store"),
                "transaction_type": tx.transaction_type,
                "amount": float(tx.amount),
                "balance_after": float(tx.balance_after),
                "note": tx.note or "",
                "bill_invoice_no": tx.bill_invoice_no or "",
                "created_at": tx.created_at.isoformat() if tx.created_at else None
            })

    all_txs.sort(key=lambda t: t["created_at"] or "", reverse=True)

    return {
        "total_due": float(total_due),
        "total_outstanding_dues": float(total_due),
        "stores_count": len(stores_list),
        "accounts_count": len(stores_list),
        "stores": stores_list,
        "stores_breakdown": stores_list,
        "transactions": all_txs,
        "recent_entries": all_txs
    }


@app.post("/api/khata/pay-dues", tags=["Khata"])
def pay_customer_dues(payload: PayDuesRequest, db: Session = Depends(get_db)):
    """
    Executes instant UPI / Online payment against customer's pending Udhaari,
    clearing balance in SQLite and generating a verifiable digital payment receipt.
    """
    khata = db.query(Khata).filter(Khata.khata_id == payload.khata_id).first()
    if not khata:
        raise HTTPException(status_code=404, detail="Khata account not found.")

    customer = db.query(User).filter(User.user_id == payload.customer_id).first()
    cust_name = customer.full_name if customer else (khata.customer_name or "Customer")

    amt_dec = Decimal(str(payload.amount))
    bal_before = khata.current_balance
    bal_after = max(Decimal("0.00"), bal_before - amt_dec)
    khata.current_balance = bal_after
    khata.last_payment_date = datetime.utcnow()

    receipt_no = f"RCP-UPI-{int(datetime.utcnow().timestamp())}"

    tx = KhataTransaction(
        khata_id=khata.khata_id,
        transaction_type="ACCEPT_PAYMENT",
        amount=amt_dec,
        balance_before=bal_before,
        balance_after=bal_after,
        note=f"Paid via {payload.payment_method} ({payload.notes or 'Instant Clear'})",
        bill_invoice_no=receipt_no
    )
    db.add(tx)

    receipt = Receipt(
        receipt_id=receipt_no,
        shopkeeper_id=khata.shopkeeper_id,
        customer_id=payload.customer_id,
        customer_name=cust_name,
        customer_phone=khata.customer_phone,
        items_summary=json.dumps([{"name": f"Khata Dues Cleared via {payload.payment_method}", "qty": 1, "unit": "payment", "rate": payload.amount, "total": payload.amount}]),
        subtotal=amt_dec,
        discount=Decimal("0.00"),
        total_amount=amt_dec,
        payment_status="PAID_UPI",
        source="CUSTOMER_UPI_PAYMENT",
        voice_transcript=f"Paid ₹{payload.amount} online",
        is_viewed=False
    )
    db.add(receipt)

    msg = Message(
        sender_id=payload.customer_id,
        receiver_id=khata.shopkeeper_id,
        shopkeeper_id=khata.shopkeeper_id,
        customer_id=payload.customer_id,
        sender_role="customer",
        content=f"💰 Payment Received: ₹{payload.amount:.2f} received via {payload.payment_method} from {cust_name}. Remaining Khata balance: ₹{float(bal_after):.2f}.",
        message_type="PAYMENT_ALERT",
        is_read=False
    )
    db.add(msg)
    db.commit()

    return {
        "status": "SUCCESS",
        "message": f"Successfully paid ₹{payload.amount:.2f} via {payload.payment_method}.",
        "receipt_id": receipt_no,
        "balance_before": float(bal_before),
        "new_balance": float(bal_after),
        "receipt": {
            "receipt_id": receipt_no,
            "amount_paid": payload.amount,
            "method": payload.payment_method,
            "customer_name": cust_name,
            "remaining_due": float(bal_after),
            "created_at": receipt.created_at.isoformat()
        }
    }


@app.get("/api/messages", tags=["Messages"])
def get_messages(user_id: int, partner_id: Optional[int] = None, db: Session = Depends(get_db)):
    """Fetches chat conversation between user and partner or user's general messages."""
    query = db.query(Message)
    if partner_id:
        query = query.filter(
            ((Message.sender_id == user_id) & (Message.receiver_id == partner_id)) |
            ((Message.sender_id == partner_id) & (Message.receiver_id == user_id))
        )
    else:
        query = query.filter((Message.sender_id == user_id) | (Message.receiver_id == user_id))

    messages = query.order_by(asc(Message.created_at)).all()

    for m in messages:
        if m.receiver_id == user_id and not m.is_read:
            m.is_read = True
    db.commit()

    res = []
    for m in messages:
        sender = db.query(User).filter(User.user_id == m.sender_id).first()
        res.append({
            "message_id": m.message_id,
            "sender_id": m.sender_id,
            "sender_name": sender.full_name if sender else "User",
            "sender_role": m.sender_role,
            "receiver_id": m.receiver_id,
            "content": m.content,
            "message_type": m.message_type,
            "is_read": m.is_read,
            "created_at": m.created_at.isoformat() if m.created_at else None
        })
    return {"messages": res}


@app.post("/api/messages", tags=["Messages"])
def send_message(payload: SendMessageRequest, db: Session = Depends(get_db)):
    """Sends direct inquiry from Customer to Shopkeeper or response from Shopkeeper."""
    sender = db.query(User).filter(User.user_id == payload.sender_id).first()
    receiver = db.query(User).filter(User.user_id == payload.receiver_id).first()

    if not sender or not receiver:
        raise HTTPException(status_code=404, detail="Sender or Receiver not found.")

    shopkeeper_id = sender.user_id if sender.role == "shopkeeper" else receiver.user_id
    customer_id = sender.user_id if sender.role == "customer" else receiver.user_id

    msg = Message(
        sender_id=payload.sender_id,
        receiver_id=payload.receiver_id,
        shopkeeper_id=shopkeeper_id,
        customer_id=customer_id,
        sender_role=sender.role,
        content=payload.content.strip(),
        message_type=payload.message_type or "TEXT",
        is_read=False
    )
    db.add(msg)
    db.commit()
    db.refresh(msg)

    return {
        "status": "SENT",
        "message": {
            "message_id": msg.message_id,
            "sender_id": msg.sender_id,
            "sender_name": sender.full_name,
            "sender_role": msg.sender_role,
            "receiver_id": msg.receiver_id,
            "content": msg.content,
            "message_type": msg.message_type,
            "created_at": msg.created_at.isoformat()
        }
    }


@app.get("/api/messages/conversations", tags=["Messages"])
def get_conversations(user_id: int, db: Session = Depends(get_db)):
    """Returns conversation list for user with last message and unread counts."""
    sent_partners = [row[0] for row in db.query(Message.receiver_id).filter(Message.sender_id == user_id).distinct()]
    received_partners = [row[0] for row in db.query(Message.sender_id).filter(Message.receiver_id == user_id).distinct()]
    all_partners = set(sent_partners + received_partners)

    conversations = []
    for pid in all_partners:
        partner = db.query(User).filter(User.user_id == pid).first()
        if not partner:
            continue

        last_msg = db.query(Message).filter(
            ((Message.sender_id == user_id) & (Message.receiver_id == pid)) |
            ((Message.sender_id == pid) & (Message.receiver_id == user_id))
        ).order_by(desc(Message.created_at)).first()

        unread = db.query(Message).filter(
            Message.sender_id == pid,
            Message.receiver_id == user_id,
            Message.is_read == False
        ).count()

        conversations.append({
            "partner_id": partner.user_id,
            "partner_name": partner.shop_name or partner.full_name,
            "owner_name": partner.full_name,
            "partner_role": partner.role,
            "avatar_url": partner.avatar_url,
            "last_message": last_msg.content if last_msg else "",
            "last_message_time": last_msg.created_at.isoformat() if last_msg and last_msg.created_at else None,
            "unread_count": unread
        })

    conversations.sort(key=lambda c: c["last_message_time"] or "", reverse=True)
    return {"conversations": conversations}


@app.get("/api/receipts/customer/{customer_id}", tags=["Receipts"])
def get_customer_receipts(customer_id: int, db: Session = Depends(get_db)):
    """Returns all digital receipts generated for a customer (voice POS, counter, etc.)."""
    cust = db.query(User).filter(User.user_id == customer_id).first()
    query = db.query(Receipt).filter(Receipt.customer_id == customer_id)
    if cust and cust.phone_number:
        query = db.query(Receipt).filter((Receipt.customer_id == customer_id) | (Receipt.customer_phone == cust.phone_number) | (Receipt.customer_name.ilike(f"%{cust.full_name}%")))

    receipts = query.order_by(desc(Receipt.created_at)).all()
    res = []
    for r in receipts:
        sk = db.query(User).filter(User.user_id == r.shopkeeper_id).first()
        try:
            items = json.loads(r.items_summary)
        except Exception:
            items = [{"name": r.items_summary, "qty": 1, "unit": "item", "rate": float(r.total_amount), "total": float(r.total_amount)}]

        res.append({
            "receipt_id": r.receipt_id,
            "shopkeeper_id": r.shopkeeper_id,
            "shop_name": sk.shop_name if sk and sk.shop_name else (sk.full_name if sk else "Kirana Store"),
            "shop_phone": sk.phone_number if sk else "",
            "shop_address": sk.address if sk else "Mandsaur",
            "customer_name": r.customer_name,
            "customer_phone": r.customer_phone,
            "items": items,
            "subtotal": float(r.subtotal),
            "discount": float(r.discount),
            "total_amount": float(r.total_amount),
            "payment_status": r.payment_status,
            "source": r.source,
            "voice_transcript": r.voice_transcript,
            "is_viewed": r.is_viewed,
            "created_at": r.created_at.isoformat() if r.created_at else None
        })
    return {"receipts": res}


@app.get("/api/receipts/{receipt_id}", tags=["Receipts"])
def get_receipt_details(receipt_id: str, db: Session = Depends(get_db)):
    """Returns full printable receipt details."""
    r = db.query(Receipt).filter(Receipt.receipt_id == receipt_id).first()
    if not r:
        raise HTTPException(status_code=404, detail="Receipt not found.")

    sk = db.query(User).filter(User.user_id == r.shopkeeper_id).first()
    try:
        items = json.loads(r.items_summary)
    except Exception:
        items = [{"name": r.items_summary, "qty": 1, "unit": "item", "rate": float(r.total_amount), "total": float(r.total_amount)}]

    return {
        "receipt_id": r.receipt_id,
        "shopkeeper_id": r.shopkeeper_id,
        "shop_name": sk.shop_name if sk and sk.shop_name else (sk.full_name if sk else "Kirana Store"),
        "shop_phone": sk.phone_number if sk else "",
        "shop_address": sk.address if sk else "Mandsaur",
        "customer_name": r.customer_name,
        "customer_phone": r.customer_phone,
        "items": items,
        "subtotal": float(r.subtotal),
        "discount": float(r.discount),
        "total_amount": float(r.total_amount),
        "payment_status": r.payment_status,
        "source": r.source,
        "voice_transcript": r.voice_transcript,
        "created_at": r.created_at.isoformat() if r.created_at else None
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
