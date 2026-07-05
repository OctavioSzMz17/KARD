
"""
MCP tool definitions for the products module (Rules.md section 14).

Stage 1 is intentionally **read-heavy**: agents may browse and create
catalog entries, nudge prices/metadata, and record sales — but they
may NOT delete products or touch inventory in bulk. `delete_product`
and `restock_product` stay server-only (Postman/UI), matching Rules.md
section 14.5 ("Do NOT expose database/deletion tools to AI agents").

This module only registers the *shape* of each tool — description,
the handler it ultimately calls, its expected arguments, and whether
it requires an authenticated caller. Wiring these into an actual MCP
server/agent runtime is a separate, later integration step; nothing
here is imported or executed by the FastAPI app itself.

Every handler is a bound `ProductService` method, and every one of
them resolves `tenant_id`/`user_id` from the verified `CurrentUser`
passed in by the MCP runtime — never from agent-supplied arguments
(Rules.md section 14.4 mirrors section 7's "never trust the caller").
"""

from products.service import ProductService

PRODUCT_TOOLS = {
    "create_product": {
        "description": "Create a new product in the caller's tenant catalog.",
        "handler": ProductService.create_product,
        "args_model": "products.models.ProductCreate",
        "requires_auth": True,
    },
    "list_products": {
        "description": "List/search the caller's tenant products with pagination.",
        "handler": ProductService.list_products,
        "args_model": None,  # page, page_size, search, product_type — plain kwargs
        "requires_auth": True,
    },
    "get_product": {
        "description": "Fetch a single product by id from the caller's tenant catalog.",
        "handler": ProductService.get_product,
        "args_model": None,  # product_id: str
        "requires_auth": True,
    },
    "update_product": {
        "description": "Update a product's name, description, price, threshold, or metadata.",
        "handler": ProductService.update_product,
        "args_model": "products.models.ProductUpdate",
        "requires_auth": True,
    },
    "sell_product": {
        "description": "Record a sale: decrease stock by a quantity (never below zero).",
        "handler": ProductService.sell_product,
        "args_model": "products.models.StockChangeRequest",
        "requires_auth": True,
    },
}

# Deliberately absent from PRODUCT_TOOLS (Rules.md section 14.5 / Stage 1 scope):
#   - delete_product   (soft delete is still a destructive, audited operation)
#   - restock_product  (inventory increases are a human/owner decision)
#   - low_stock        (dashboard widget; not an agent-facing read for Stage 1)


def list_tool_names() -> list[str]:
    """Convenience accessor — names of every tool an agent runtime may register."""
    return list(PRODUCT_TOOLS.keys())
