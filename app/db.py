import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from models import Base

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./data.db")
engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

def init_db():
    Base.metadata.create_all(bind=engine)
```

### services.py
```python
from sqlalchemy.orm import Session
from models import User, Setting

def ensure_user(db: Session, discord_id: str) -> User:
    u = db.get(User, discord_id)
    if not u:
        u = User(discord_id=discord_id)
        db.add(u); db.commit(); db.refresh(u)
    return u

def get_settings(db: Session) -> Setting:
    s = db.get(Setting, 1)
    if not s:
        s = Setting(id=1, bank_name="미설정", account_number="미설정", holder="미설정", total_stock_rbx=0)
        db.add(s); db.commit(); db.refresh(s)
    return s

def stock_summary_text(db: Session) -> str:
    s = get_settings(db)
    inner = f"총 재고: {s.total_stock_rbx} R$"
    return f"재고 : ```{inner}```\n아래 버튼을 눌려 이용해 주세요"

def set_or_inc_stock(db: Session, value: int, mode: str = "set"):
    s = get_settings(db)
    if mode == "set":
        s.total_stock_rbx = max(0, value)
    else:
        s.total_stock_rbx = max(0, (s.total_stock_rbx or 0) + value)
    db.commit()

def dec_stock_exact(db: Session, need: int) -> tuple[bool, str]:
    if need <= 0:
        return False, "요청 수량이 0 이하야."
    s = get_settings(db)
    if s.total_stock_rbx < need:
        return False, f"재고가 부족해. 현재 재고는 {s.total_stock_rbx} R$야."
    s.total_stock_rbx -= need
    db.commit()
    return True, "OK"
