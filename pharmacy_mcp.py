from mcp.server.fastmcp import FastMCP
from pharmacy_functions import get_drug_info, place_order, lookup_order

# Initialize the MCP server
mcp = FastMCP("Pharmacy Agent")

@mcp.tool()
def drug_info(drug_name: str) -> dict:
    """
    Get detailed information about a specific drug.
    
    Args:
        drug_name: Name of the drug to look up.
    """
    return get_drug_info(drug_name)

@mcp.tool()
def order_drug(customer_name: str, drug_name: str) -> dict:
    """
    Place a new prescription order for a customer.
    
    Args:
        customer_name: Customer's full name.
        drug_name: Name of the drug to order.
    """
    return place_order(customer_name, drug_name)

@mcp.tool()
def check_order_status(order_id: int) -> dict:
    """
    Look up an existing order by its ID.
    
    Args:
        order_id: The order ID number to look up.
    """
    return lookup_order(order_id)

if __name__ == "__main__":
    mcp.run()
