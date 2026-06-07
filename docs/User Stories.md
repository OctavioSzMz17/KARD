# User Stories

---

## **STAGE 1: USER STORIES**

### **1. AUTHENTICATION & TENANT IDENTIFICATION**

**US-001: Admin Login with Tenant Detection**

- As an **Admin-OwnerSAAS**, I want to log in with email/password, so that the system identifies my tenant and loads my business data
- **Acceptance Criteria:**
    - Login form accepts email + password
    - System queries database for tenant association by email
    - Token is generated containing tenant_id, role, business_type (restaurant/optician)
    - On successful login, user is redirected to their Dynamic Dashboard
    - If tenant doesn't exist, error message: "No business associated with this account"

**US-002: Persistent Tenant Context via Token**

- As a **Developer**, I want the token to store tenant_id + business_type, so that every API call knows which tenant's data to fetch
- **Acceptance Criteria:**
    - JWT includes: user_id, tenant_id, business_type, role
    - Frontend stores token in secure storage (httpOnly cookie)
    - All API requests include Authorization header
    - Backend middleware extracts tenant_id from token and filters queries accordingly

**US-003: Register New Business (SaaS Owner)**

- As a **SaaS Owner**, I want to register a new business (restaurant/optician), so that I can add new tenants to the system
- **Acceptance Criteria:**
    - Form fields: business_name, business_type (dropdown: restaurant/optician), email, password, phone
    - System creates new tenant record with unique tenant_id
    - Creates admin user linked to that tenant
    - Sends confirmation email
    - Admin redirected to onboarding/customization flow

---

### **2. ADMIN - PRODUCT CRUD (Multi-tenant + JSONB)**

**US-004: Create Product with Tenant-Specific Metadata**

- As an **Admin**, I want to add a product with business-specific details, so that I can build my product catalog
- **Acceptance Criteria:**
    - Form auto-detects business_type from token
    - **For Restaurant**: fields = name, description, price, unit_of_measure, perishable (checkbox), expiry_days
    - **For Optician**: fields = name, description, price, sphere, cylinder, axis
    - System stores: `product_id (prefixed: p_XXX{business_type}_{tenant_id}), name, price, metadata (JSONB)`
    - On success: product appears in catalog, success snackBar notification
    - Validation: name required, price > 0, metadata fields required per type

**US-005: View All Products in Catalog**

- As an **Admin**, I want to see all my products in a table/list, so that I can manage my inventory
- **Acceptance Criteria:**
    - Table displays: product_id, name, price, type, inventory_count, actions (edit/delete/stock)
    - JSONB metadata is readable (e.g., "Perishable: Yes, Expires in 30 days")
    - Pagination: 10-25 items per page
    - Search by name/product_id
    - Filter by product type (if applicable)

**US-006: Edit Product**

- As an **Admin**, I want to update product details and metadata, so that my catalog stays current
- **Acceptance Criteria:**
    - Click "Edit" opens form pre-filled with current data
    - Can modify name, price, and business-specific metadata
    - Cannot change product_id or business_type
    - On save: confirm changes, update timestamp
    - Validation same as US-004

**US-007: Delete Product**

- As an **Admin**, I want to remove a product from the catalog, so that obsolete items disappear
- **Acceptance Criteria:**
    - Click "Delete" shows confirmation dialog
    - On confirm: product is soft-deleted (marked inactive, not hard-deleted)
    - Product disappears from catalog view
    - Success notification

**US-008: Manage Inventory (Decrease Stock)**

- As an **Admin**, I want to decrease product inventory when sold/used, so that I track stock accurately
- **Acceptance Criteria:**
    - Each product has `inventory_count` field
    - Click "Sell" or "Use" opens modal with quantity input
    - Enter quantity and click "Confirm"
    - System decreases inventory_count by quantity
    - If inventory reaches 0, product marked as "Out of Stock"
    - Low-stock warning if inventory < threshold (e.g., < 5 units)
    - Audit log: tracks each transaction (date, quantity, user)

---

### **3. DYNAMIC DASHBOARD**

**US-009: Admin Dashboard - Overview**

- As an **Admin**, I want to see key metrics on my dashboard, so that I understand my business at a glance
- **Acceptance Criteria:**
    - Dashboard loads on login
    - Displays:
        - Total products
        - Total inventory count
        - Products out of stock
        - Recent sales/transactions (last 7 days)
    - Metrics auto-update when data changes
    - Responsive design (mobile-friendly)

**US-010: Dashboard - Inventory Alert Widget**

- As an **Admin**, I want to see low-stock alerts, so that I can reorder before running out
- **Acceptance Criteria:**
    - Widget displays products with inventory < 5 units
    - Color-coded: red = 0 units, orange = 1-4 units, yellow = 5-10 units
    - Click alert to jump to product edit/restock modal
    - Shows count of alerts (e.g., "3 products need attention")

**US-011: Dashboard - Business-Type Specific Widgets**

- As an **Admin**, I want to see relevant metrics for my business type, so that my dashboard is useful
- **Acceptance Criteria:**
    - **Restaurant**: sales by category, perishable expiry warnings, top-selling items
    - **Optician**: inventory by lens type (sphere/cylinder/axis), prescription demand trends, sales by prescription type
    - Widgets load based on business_type from token

---

### **4. PRODUCT CATALOG (Public/Customer View - Prep for Stage 3)**

**US-012: View Product Catalog (Admin Interface)**

- As an **Admin**, I want a searchable, filterable product catalog, so that I can quickly find products
- **Acceptance Criteria:**
    - Displays all active products in grid or table view
    - Search by name, product_id, or metadata (e.g., "perishable", "sphere -2.00")
    - Filter by stock status (in stock, low stock, out of stock)
    - Sort by name, price, date added, stock level
    - Each product shows: image (if available), name, price, stock count, quick actions (edit/delete/sell)

**US-013: Product Detail View**

- As an **Admin**, I want to see full product details including metadata, so that I can review all information
- **Acceptance Criteria:**
    - Modal/page shows: product_id, name, description, price, inventory_count, created_date, last_modified
    - JSONB metadata displayed in readable format (e.g., "Lens Type: Contact Lens, Sphere: -2.00, Cylinder: -0.50, Axis: 180")
    - Shows audit log of inventory changes
    - Edit/Delete buttons available

---

### **5. CUSTOMIZATION / THEME (Per Tenant)**

**US-014: Admin Customization Panel - Colors & Branding**

- As an **Admin**, I want to customize my dashboard colors and branding, so that it matches my business
- **Acceptance Criteria:**
    - Settings/Customization page accessible from navbar
    - Inputs for: primary_color, secondary_color, accent_color (color pickers)
    - Upload business logo (image upload)
    - Input for business name displayed in navbar
    - Preview live changes in sidebar
    - On save: settings stored in tenant_config table, applied immediately

**US-015: Theme Persistence & Token-Based Application**

- As a **Developer**, I want the theme to load based on tenant token, so that each business sees their custom branding
- **Acceptance Criteria:**
    - On app load, frontend fetches tenant_config using tenant_id from token
    - CSS variables injected globally (--primary-color, --secondary-color, etc.)
    - All UI components reference these variables
    - Logo, business name updated in navbar
    - Changes persist across sessions

**US-016: Preset Themes (Optional - For Quick Setup)**

- As an **Admin**, I want to choose from preset color schemes, so that I don't have to pick colors manually
- **Acceptance Criteria:**
    - Preset options: Modern Blue, Restaurant Red, Professional Black, Health Green
    - Click preset to apply all colors at once
    - Can still customize individual colors after applying preset
    - Presets saved in database for future reuse

**US-017: Icons & Widgets Customization**

- As an **Admin**, I want to customize dashboard widgets (show/hide), so that I see only relevant information
- **Acceptance Criteria:**
    - Customization page has toggles for each widget (inventory alerts, sales, inventory status, etc.)
    - Drag-and-drop reordering of widgets on dashboard
    - Settings saved per tenant
    - Dashboard reflects changes immediately

---

### **SUMMARY**

| Category | Count | Stories |
| --- | --- | --- |
| Authentication | 3 | US-001, US-002, US-003 |
| Product CRUD | 5 | US-004 to US-008 |
| Dashboard | 3 | US-009 to US-011 |
| Catalog | 2 | US-012, US-013 |
| Customization | 4 | US-014 to US-017 |
| **TOTAL** | **17** | - |