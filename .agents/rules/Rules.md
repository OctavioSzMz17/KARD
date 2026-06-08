# Rules

---

Rules for IDE Agent

### **TECHNICAL RULES & SECURITY GUIDELINES (FASTAPI + PYTHON)**

### **1. AUTHENTICATION & AUTHORIZATION**

1. Use FastAPI's `Depends()` for authentication middleware
2. JWT tokens validated in every protected endpoint via dependency injection
3. Create `auth.py` with `get_current_user()` function that extracts and validates token
4. Password hashing with `passlib` + bcrypt (min 12 rounds)
5. Store JWT secret in environment variables (`.env` file, never hardcoded)
6. Token payload must contain: `user_id`, `tenant_id`, `business_type`, `role`, `iat`, `exp`
7. Implement token blacklist on logout (Redis or in-memory store)
8. Failed login attempts: use `RedisCounter` to track and lock after 5 attempts
9. All sensitive operations require re-authentication confirmation
10. **Tenant creation and user creation are separate operations** (US-003 vs. US-018):
    - `POST /auth/register` creates ONLY the `tenants` record — a business can exist before it has any login-capable user
    - Users (including a brand-new tenant's first admin, and every staff member added afterward) are created through one shared "create user" flow that explicitly takes `tenant_id` + `role`
    - This keeps "register a new business" and "this business just hired someone" the same mechanism, so onboarding additional staff later requires no special-casing
    - A `business_admin` creating staff must have `tenant_id` taken from their own JWT (never from the request body — see section 7); a `saas_admin` may pick any active tenant explicitly

### **2. FASTAPI DEPENDENCY INJECTION & MIDDLEWARE**

1. Create `dependencies.py` for reusable dependencies (auth, tenant validation)
2. All protected routes must include `Depends(get_current_user)` parameter
3. All database queries must include `Depends(get_tenant_id)` to extract tenant_id from token
4. Middleware to log every request: timestamp, user_id, tenant_id, endpoint, method, status
5. Middleware to add security headers: `Content-Security-Policy`, `X-Frame-Options`, `X-Content-Type-Options`
6. CORS configuration: allow only frontend domain, credentials: true

### **3. DATABASE & REPOSITORY LAYER (`repository.py`)**

1. Use SQLAlchemy ORM (not raw SQL) to prevent SQL injection
2. Every query in `repository.py` must filter by `tenant_id`
3. Never accept `tenant_id` as parameter from request; extract from token only
4. Use `get_session()` dependency to manage database sessions
5. All database operations should use prepared statements (SQLAlchemy handles this)
6. Repository methods signature: `def get_products(self, tenant_id: str, user_id: str) -> List[Product]:`
7. Implement transaction management for multi-step operations
8. Log all database operations (INSERT, UPDATE, DELETE) with user_id, tenant_id, timestamp
9. Soft delete pattern: add `is_deleted` boolean column, filter with `is_deleted=False` in queries
10. Add `created_at`, `updated_at`, `created_by` timestamps to all tables

### **4. SERVICE LAYER (`service.py`)**

1. All business logic lives in service.py, NOT in routes.py
2. Service methods should NOT handle HTTP requests; work with Python objects only
3. Validate all inputs at service level before database operations
4. Service methods must accept `tenant_id` and `user_id` as parameters
5. Example: `ProductService.create_product(tenant_id, user_id, product_data)`
6. Use type hints: `def create_product(self, tenant_id: str, user_id: str, data: ProductCreate) -> Product:`
7. Raise custom exceptions for business rule violations (e.g., `InsufficientStockError`)
8. Never catch all exceptions silently; log and re-raise appropriately

### **5. ROUTES & ENDPOINTS (`routes.py`)**

1. Routes should only handle HTTP layer: receive request, call service, return response
2. All protected endpoints must have `current_user: User = Depends(get_current_user)` parameter
3. Extract tenant_id from current_user.tenant_id, NOT from request body
4. Use Pydantic models for request validation (defined in `models.py`)
5. Return consistent response format: `{"success": bool, "data": {...}, "error": str or null}`
6. HTTP status codes: 200 (success), 400 (bad request), 401 (unauthorized), 403 (forbidden), 404 (not found), 500 (server error)
7. Never return raw database objects; use response models for serialization
8. Example route:

```python
@router.post("/", status_code=201)
      async def crear_producto(
          producto: ProductCreate,
          current_user: User = Depends(get_current_user)
      ):
          try:
              result = ProductService.create_product(
                  current_user.tenant_id,
                  current_user.user_id,
                  producto
              )
              return {"success": True, "data": result}
          except Exception as e:
              logger.error(f"Error creating product: {e}")
              return {"success": False, "error": "Failed to create product"}
```

### **6. PYDANTIC MODELS (`models.py`)**

1. Define separate models: `ProductCreate`, `ProductUpdate`, `ProductResponse`
2. Use validators for: email format, price > 0, name length, JSONB schema
3. Example:

```python
from pydantic import BaseModel, validator
      
      class ProductCreate(BaseModel):
          nombre: str
          precio: float
          metadata: dict
          
          @validator('precio')
          def validate_precio(cls, v):
              if v <= 0:
                  raise ValueError('Price must be positive')
              return v
          
          @validator('nombre')
          def validate_nombre(cls, v):
              if len(v) > 500:
                  raise ValueError('Name too long')
              return v
```

1. Never expose internal fields in response models (e.g., no passwords, no internal IDs)
2. Use `response_model=ProductResponse` on routes for output serialization

### **7. MULTI-TENANCY ISOLATION**

1. Every repository method signature: `def get_product(self, tenant_id: str, product_id: str)`
2. Every query must include: `WHERE tenant_id = ? AND product_id = ?`
3. Use database constraint: `UNIQUE(tenant_id, product_id)` to prevent collisions
4. Never trust `tenant_id` from request; always extract from JWT token
5. Test: attempt to access another tenant's data, should return 404 or 403
6. Add middleware to log any attempts to access cross-tenant data (security alert)

### **8. JSONB SCHEMA ENFORCEMENT**

1. Define JSONB schemas in `models.py`:

```python
RESTAURANT_METADATA_SCHEMA = {
          "type": "object",
          "properties": {
              "unidad_medida": {"type": "string"},
              "perecedero": {"type": "boolean"},
              "dias_expiracion": {"type": "integer"}
          },
          "required": ["unidad_medida", "perecedero"]
      }
      
      OPTICIAN_METADATA_SCHEMA = {
          "type": "object",
          "properties": {
              "esfera": {"type": "string"},
              "cilindro": {"type": "string"},
              "eje": {"type": "integer"}
          },
          "required": ["esfera", "cilindro"]
      }
```

1. Validate JSONB in service layer before save:

```python
from jsonschema import validate, ValidationError
      
      def validate_metadata(business_type: str, metadata: dict):
          schema = RESTAURANT_METADATA_SCHEMA if business_type == "restaurant" else OPTICIAN_METADATA_SCHEMA
          try:
              validate(instance=metadata, schema=schema)
          except ValidationError as e:
              raise ValueError(f"Invalid metadata: {e.message}")
```

1. Reject any extra keys not in schema

### **9. INPUT VALIDATION & SANITIZATION**

1. Pydantic models validate at endpoint entry point (automatic)
2. Service layer does secondary validation (business rules)
3. Email: use `EmailStr` from pydantic
4. Prices: ensure `price: float` with min/max constraints
5. Strings: max length validators, no special characters (except where needed)
6. JSONB: validate schema before storage (see rule 8)
7. Example:

```python
from pydantic import EmailStr, validator
      
      class ProductCreate(BaseModel):
          nombre: str = Field(..., max_length=500)
          precio: float = Field(..., gt=0)
          metadata: dict
      
      class UserRegister(BaseModel):
          email: EmailStr
          password: str = Field(..., min_length=8)
```

### **10. ERROR HANDLING & LOGGING**

1. Create custom exceptions in `exceptions.py`:

```python
      class TenantAccessError(Exception):
          pass
      
      class ProductNotFoundError(Exception):
          pass
```

1. Use FastAPI exception handlers:

```python
      @app.exception_handler(ProductNotFoundError)
      async def product_not_found(request, exc):
          return JSONResponse(
              status_code=404,
              content={"success": False, "error": "Product not found"}
          )
```

1. Log all errors with `logging` module: timestamp, user_id, tenant_id, full stack trace
2. Never expose internal details in error messages to user
3. Generic error to user: "Something went wrong", detailed error in logs only

### **11. AUDIT LOGGING**

1. Create `audit.py` module for logging all operations
2. Log every CRUD operation:

```python
def log_operation(tenant_id: str, user_id: str, action: str, resource_id: str, before: dict = None, after: dict = None):
          logger.info(f"AUDIT | tenant_id={tenant_id} | user_id={user_id} | action={action} | resource_id={resource_id}")
```

1. Call in service layer after every operation:

```python
ProductService.create_product(tenant_id, user_id, data):
          product = repository.create(tenant_id, data)
          audit_log(tenant_id, user_id, "CREATE_PRODUCT", product.id, after=product.dict())
          return product
```

1. Log authentication: success/failure, timestamp, IP address, email
2. Use structured logging (JSON format) for easier parsing

### **12. DATABASE MODELS (SQLAlchemy)**

1. Define in `db/models.py`:

```python
from sqlalchemy import Column, String, Float, JSON, DateTime, Boolean
      from datetime import datetime
      
      class Product(Base):
          __tablename__ = "products"
          
          id = Column(String, primary_key=True)
          tenant_id = Column(String, nullable=False, index=True)
          user_id = Column(String, nullable=False)
          nombre = Column(String(500), nullable=False)
          precio = Column(Float, nullable=False)
          metadata = Column(JSON, nullable=False)  # JSONB in PostgreSQL
          is_deleted = Column(Boolean, default=False)
          created_at = Column(DateTime, default=datetime.utcnow)
          updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
          
          __table_args__ = (
              UniqueConstraint('tenant_id', 'id', name='unique_product_per_tenant'),
          )
```

1. Always include: `tenant_id`, `created_at`, `updated_at`, `created_by`
2. Use indexes on frequently queried columns: `tenant_id`, `user_id`, `created_at`

### **13. ENVIRONMENT VARIABLES (`.env` file)**

1. Never commit `.env` to git (add to `.gitignore`)
2. Required variables:
    
    ```json
    DATABASE_URL=postgresql://user:password@localhost/kard_db
          JWT_SECRET_KEY=your-secret-key-min-32-chars
          JWT_ALGORITHM=HS256
          JWT_EXPIRATION_HOURS=24
          REDIS_URL=redis://localhost:6379
          ENVIRONMENT=development
          LOG_LEVEL=INFO
          CORS_ORIGINS=http://localhost:3000
    ```
    
3. Load with: `from dotenv import load_dotenv; load_dotenv()`

### **14. MCP TOOLS (`mcp_tools.py`)**

1. Register only safe, non-sensitive operations
2. Each tool must validate tenant_id and user permissions
3. Example:

```json
PRODUCT_TOOLS = {
          "crear_producto": {
              "description": "Crea un producto en el catálogo",
              "handler": ProductService.create_product,
              "requires_auth": True
          }
      }
```

1. All MCP tool handlers must include tenant_id validation
2. Do NOT expose database/deletion tools to AI agents (Stage 1)

### **15. TESTING**

1. Use `pytest` for unit and integration tests
2. Structure: `tests/test_products.py`, `tests/test_auth.py`, etc.
3. Test cross-tenant access (should fail):

```python
def test_user_cannot_access_other_tenant_products(client, db_session):
          user1_token = get_token(user1)
          product = create_product_for_tenant(tenant2)
          response = client.get(f"/products/{product.id}", headers={"Authorization": f"Bearer {user1_token}"})
          assert response.status_code == 404
```

1. Use test fixtures for common setup (users, tenants, products)
2. Use `@pytest.mark.asyncio` for async route testing
3. Mock external dependencies (Redis, email service, etc.)

### **16. FOLDER STRUCTURE (COMPLETE)**

```python
kard/
   ├── main.py                      # FastAPI app initialization
   ├── .env                         # Environment variables (NOT in git)
   ├── .env.example                 # Example env file (for reference)
   ├── requirements.txt             # Python dependencies
   ├── core/
   │   ├── __init__.py
   │   ├── config.py                # Settings from .env
   │   ├── security.py              # JWT, password hashing
   │   ├── dependencies.py          # Shared dependencies (auth, tenant)
   │   └── exceptions.py            # Custom exceptions
   ├── db/
   │   ├── __init__.py
   │   ├── database.py              # SQLAlchemy setup, session
   │   └── models.py                # SQLAlchemy models
   ├── auth/
   │   ├── __init__.py
   │   ├── routes.py                # Login, register endpoints
   │   ├── service.py               # Auth business logic
   │   ├── models.py                # User schemas
   │   └── repository.py            # User database queries
   ├── products/
   │   ├── __init__.py
   │   ├── routes.py                # /products endpoints
   │   ├── service.py               # Product business logic
   │   ├── models.py                # ProductCreate, ProductResponse
   │   ├── repository.py            # Product queries
   │   └── mcp_tools.py             # MCP tool definitions
   ├── businessadmin/
   │   ├── __init__.py
   │   ├── routes.py
   │   ├── service.py
   │   ├── models.py
   │   └── repository.py
   ├── saasadmin/
   │   ├── __init__.py
   │   ├── routes.py
   │   ├── service.py
   │   ├── models.py
   │   └── repository.py
   ├── logging/
   │   ├── __init__.py
   │   ├── audit.py                 # Audit logging functions
   │   └── logger.py                # Logger setup
   ├── utils/
   │   ├── __init__.py
   │   ├── validators.py            # Validation helpers
   │   └── helpers.py               # Utility functions
   └── tests/
       ├── __init__.py
       ├── test_auth.py
       ├── test_products.py
       ├── conftest.py              # Pytest fixtures
       └── factories.py             # Test data factories
```

### **17. MAIN APP INITIALIZATION (`main.py`)**

```python
from fastapi import FastAPI
   from fastapi.middleware.cors import CORSMiddleware
   from core.config import settings
   from core.exceptions import TenantAccessError
   from auth.routes import router as auth_router
   from products.routes import router as products_router
   from logging.logger import setup_logging
   
   setup_logging()
   app = FastAPI(title="KARD", version="1.0.0")
   
   # CORS
   app.add_middleware(
       CORSMiddleware,
       allow_origins=[settings.CORS_ORIGINS],
       allow_credentials=True,
       allow_methods=["*"],
       allow_headers=["*"]
   )
   
   # Exception handlers
   @app.exception_handler(TenantAccessError)
   async def tenant_access_exception(request, exc):
       return JSONResponse(status_code=403, content={"success": False, "error": "Access denied"})
   
   # Routers
   app.include_router(auth_router, prefix="/api/v1/auth", tags=["auth"])
   app.include_router(products_router, prefix="/api/v1/products", tags=["products"])
   
   if __name__ == "__main__":
       import uvicorn
       uvicorn.run(app, host="0.0.0.0", port=8000)
```

### **18. EXAMPLE: COMPLETE PRODUCT FLOW**

```python
# models.py
   from pydantic import BaseModel, Field, validator
   
   class ProductCreate(BaseModel):
       nombre: str = Field(..., max_length=500)
       precio: float = Field(..., gt=0)
       metadata: dict
   
   # service.py
   from db.database import get_db
   from products.repository import ProductRepository
   from audit import log_operation
   
   class ProductService:
       @staticmethod
       def create_product(tenant_id: str, user_id: str, data: ProductCreate):
           # Validate metadata
           validate_metadata(tenant_id, data.metadata)
           
           # Save to database
           product = ProductRepository.create(tenant_id, user_id, data)
           
           # Audit log
           log_operation(tenant_id, user_id, "CREATE_PRODUCT", product.id, after=product.dict())
           
           return product
   
   # repository.py
   from db.models import Product
   
   class ProductRepository:
       @staticmethod
       def create(tenant_id: str, user_id: str, data: ProductCreate):
           product = Product(
               id=f"p_{uuid.uuid4().hex[:6]}{tenant_id}",
               tenant_id=tenant_id,
               user_id=user_id,
               nombre=data.nombre,
               precio=data.precio,
               metadata=data.metadata
           )
           db_session.add(product)
           db_session.commit()
           return product
   
   # routes.py
   from fastapi import APIRouter, Depends
   from core.dependencies import get_current_user
   from products.service import ProductService
   from products.models import ProductCreate, ProductResponse
   
   router = APIRouter()
   
   @router.post("/", status_code=201, response_model=ProductResponse)
   async def crear_producto(
       data: ProductCreate,
       current_user: User = Depends(get_current_user)
   ):
       try:
           product = ProductService.create_product(
               current_user.tenant_id,
               current_user.user_id,
               data
           )
           return {"success": True, "data": product}
       except ValueError as e:
           return {"success": False, "error": str(e)}, 400
```


### 19. DOCKER & CONTAINERIZATION

1. Each service must have its own Dockerfile
2. docker-compose.yml lives at the project root
3. NEVER hardcode environment variables in Dockerfile; always use .env via env_file
4. Dockerfile (backend):

FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]

5. docker-compose.yml:

services:
  backend:
    build: .
    ports: ["8000:8000"]
    env_file: .env
    depends_on: [db, redis]

  db:
    image: postgres:15
    environment:
      POSTGRES_DB: kard_db
    volumes: [postgres_data:/var/lib/postgresql/data]

  redis:
    image: redis:7-alpine
    ports: ["6379:6379"]

  agent:
    build: ./agentName
    ports: ["8001:8001"]
    env_file: .env
    depends_on: [backend]

volumes:
  postgres_data:

6. Add .dockerignore: __pycache__, .env, *.pyc, tests/
7. Backend and agent are independent services — if agent crashes, backend keeps running
8. Add healthchecks so dependent services wait until the service is actually ready
9. Use named volumes for persistent data (postgres_data); never anonymous volumes
10. Production: never use --reload flag in uvicorn CMD