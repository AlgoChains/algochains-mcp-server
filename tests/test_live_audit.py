"""
Live audit tests for AlgoChains MCP Server.
Tests broker connectivity, data providers, and hidden killer detection.
"""
import sys
import os
import asyncio

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

# Set env vars before any imports
os.environ['ALPACA_API_KEY'] = 'PK3FWSMZTLAGCYGNLUW6SQQWH2'
os.environ['ALPACA_SECRET_KEY'] = '3ACUuhxmEL4L4naKjWZkZL2J4iMfJZetBD5jxUeyTP47'
os.environ['ALPACA_BASE_URL'] = 'https://paper-api.alpaca.markets'
os.environ['POLYGON_API_KEY'] = 'RoJtX0P4LHpBOVn676uOCyv4FetoPWU6'
os.environ['MASSIVE_API_KEY'] = 'mnLeqLPIo9s3GSZaTnpb9ZrfrlrHfkP2'
os.environ['FINNHUB_API_KEY'] = 'd32vhd9r01qs3vinc93gd32vhd9r01qs3vinc940'
os.environ['ALGOCHAINS_TOOL_MODE'] = 'full'


async def test_alpaca_paper():
    print("=" * 55)
    print("  TEST 3: ALPACA PAPER ACCOUNT")
    print("=" * 55)

    from algochains_mcp.brokers.registry import BrokerRegistry
    from algochains_mcp.config import load_config

    cfg = load_config()
    print(f"Configured brokers: {cfg.available_brokers()}")

    registry = BrokerRegistry(cfg)
    results = await registry.connect_all()
    print(f"Connect results: {results}")
    print(f"Available after connect: {registry.list_available()}")

    if "alpaca" in registry.list_available():
        broker = registry.get("alpaca")

        print("\n--- Account Info ---")
        try:
            acct = await broker.get_account()
            if isinstance(acct, dict):
                for k in ["id", "status", "buying_power", "cash", "portfolio_value"]:
                    if k in acct:
                        val = str(acct[k])
                        if k == "id":
                            val = val[:12] + "..."
                        print(f"  {k}: {val}")
            else:
                print(f"  Response: {str(acct)[:300]}")
            print("  OK Account connected")
        except Exception as e:
            print(f"  FAIL get_account: {type(e).__name__}: {e}")

        print("\n--- Positions ---")
        try:
            positions = await broker.get_positions()
            print(f"  OK {len(positions)} positions")
        except Exception as e:
            print(f"  FAIL get_positions: {type(e).__name__}: {e}")

        print("\n--- Orders ---")
        try:
            orders = await broker.get_orders()
            print(f"  OK {len(orders)} orders")
        except Exception as e:
            print(f"  FAIL get_orders: {type(e).__name__}: {e}")

        print("\n--- Health Check ---")
        try:
            health = await broker.health_check()
            print(f"  OK {health}")
        except Exception as e:
            print(f"  FAIL health_check: {type(e).__name__}: {e}")
    else:
        print("FAIL Alpaca not available after connect")
        print(f"  Config key set: {bool(cfg.alpaca.api_key)}")


async def test_data_providers():
    print("\n" + "=" * 55)
    print("  TEST 4: DATA PROVIDERS (Polygon, Yahoo, Finnhub)")
    print("=" * 55)

    from algochains_mcp.data_providers.registry import DataProviderRegistry

    reg = DataProviderRegistry()
    available = reg.list_available()
    print(f"Available: {available}")

    # Health check all
    print("\n--- Health Checks ---")
    try:
        results = await reg.health_check_all()
        for name, ok in results.items():
            print(f"  {'OK' if ok else 'FAIL'} {name}")
    except Exception as e:
        print(f"  FAIL health_check_all: {type(e).__name__}: {e}")

    # Test Yahoo (no key needed)
    if "yahoo" in available:
        print("\n--- Yahoo Finance ---")
        yahoo = reg.get("yahoo")
        try:
            result = await yahoo.get_quote("AAPL")
            print(f"  OK AAPL quote: {str(result)[:200]}")
        except Exception as e:
            print(f"  FAIL yahoo get_quote: {type(e).__name__}: {e}")

    # Test Polygon
    if "polygon" in available:
        print("\n--- Polygon ---")
        polygon = reg.get("polygon")
        try:
            result = await polygon.get_quote("AAPL")
            print(f"  OK AAPL quote: {str(result)[:200]}")
        except Exception as e:
            print(f"  FAIL polygon get_quote: {type(e).__name__}: {e}")

    # Test Finnhub
    if "finnhub" in available:
        print("\n--- Finnhub ---")
        finnhub = reg.get("finnhub")
        try:
            result = await finnhub.get_quote("AAPL")
            print(f"  OK AAPL quote: {str(result)[:200]}")
        except Exception as e:
            print(f"  FAIL finnhub get_quote: {type(e).__name__}: {e}")


async def test_hidden_killers():
    print("\n" + "=" * 55)
    print("  TEST 5: DEEP HIDDEN KILLER SCAN")
    print("=" * 55)

    import algochains_mcp.server as srv

    # Test 1: Every dispatch handler can be reached without NameError
    print("\n--- Dispatch Handler Integrity ---")
    # Check that all annotation constants are defined
    annot_names = [
        "ANNOT_READ_ONLY", "ANNOT_READ_EXTERNAL", "ANNOT_WRITE_SAFE",
        "ANNOT_WRITE_DESTRUCTIVE", "ANNOT_TRADE_EXEC", "ANNOT_COMPUTE",
        "ANNOT_SEARCH",
    ]
    for name in annot_names:
        if hasattr(srv, name):
            print(f"  OK {name} defined")
        else:
            print(f"  FAIL {name} MISSING — NameError at runtime!")

    # Test 2: _text helper exists and works
    print("\n--- _text Helper ---")
    try:
        result = srv._text({"test": True, "data": [1, 2, 3]})
        print(f"  OK _text returns {type(result).__name__}")
    except Exception as e:
        print(f"  FAIL _text: {type(e).__name__}: {e}")

    # Test 3: All lazy singletons can initialize
    print("\n--- Lazy Singletons ---")
    singletons = [
        ("_get_registry", srv._get_registry),
        ("_get_validator", srv._get_validator),
        ("_get_bridge", srv._get_bridge),
        ("_get_stream_manager", srv._get_stream_manager),
        ("_get_dynamic_gateway", srv._get_dynamic_gateway),
    ]
    for name, fn in singletons:
        try:
            obj = fn()
            print(f"  OK {name} -> {type(obj).__name__}")
        except Exception as e:
            print(f"  FAIL {name}: {type(e).__name__}: {e}")

    # Async singletons
    async_singletons = [
        ("_get_massive_provider", srv._get_massive_provider),
    ]
    for name, fn in async_singletons:
        try:
            obj = await fn()
            print(f"  OK {name} -> {type(obj).__name__}")
        except Exception as e:
            print(f"  FAIL {name}: {type(e).__name__}: {e}")

    # Test 4: Check for bare except that swallows errors
    print("\n--- Bare Except Scan ---")
    import ast
    server_path = os.path.join(
        os.path.dirname(__file__), '..', 'src', 'algochains_mcp', 'server.py'
    )
    with open(server_path) as f:
        tree = ast.parse(f.read())
    
    bare_excepts = 0
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler) and node.type is None:
            bare_excepts += 1
    print(f"  {'WARN' if bare_excepts > 0 else 'OK'} {bare_excepts} bare except clauses")

    # Test 5: Check for TODO/FIXME/HACK/XXX in server.py
    print("\n--- Code Smell Scan ---")
    with open(server_path) as f:
        lines = f.readlines()
    
    markers = {"TODO": 0, "FIXME": 0, "HACK": 0, "XXX": 0, "BROKEN": 0}
    for i, line in enumerate(lines, 1):
        for marker in markers:
            if marker in line.upper() and not line.strip().startswith("#!"):
                markers[marker] += 1
    
    for marker, count in markers.items():
        if count > 0:
            print(f"  WARN {count} {marker} markers found")
    if sum(markers.values()) == 0:
        print(f"  OK No TODO/FIXME/HACK/XXX/BROKEN markers")

    # Test 6: Check for hardcoded API keys or secrets
    print("\n--- Secret Leak Scan ---")
    import re
    secret_patterns = [
        (r'["\'](?:sk|pk|api)[_-]?[a-zA-Z0-9]{20,}["\']', "Possible API key"),
        (r'password\s*=\s*["\'][^"\']+["\']', "Hardcoded password"),
        (r'secret\s*=\s*["\'][a-zA-Z0-9]{10,}["\']', "Hardcoded secret"),
    ]
    leaks = 0
    for pattern, desc in secret_patterns:
        for i, line in enumerate(lines, 1):
            if re.search(pattern, line, re.IGNORECASE):
                # Skip env var lookups and test files
                if "environ" in line or "_env(" in line or "os.getenv" in line:
                    continue
                leaks += 1
                print(f"  WARN Line {i}: {desc}")
    if leaks == 0:
        print(f"  OK No hardcoded secrets detected")


async def test_error_handling():
    print("\n" + "=" * 55)
    print("  TEST 6: ERROR HANDLING (graceful degradation)")
    print("=" * 55)

    import algochains_mcp.server as srv

    # Test missing required arguments
    print("\n--- Missing Arguments ---")
    try:
        gw = srv._get_dynamic_gateway()
        result = gw.discover("", top_k=5)
        print(f"  OK Empty query returns {len(result)} results (graceful)")
    except Exception as e:
        print(f"  WARN Empty query: {type(e).__name__}: {e}")

    # Test Massive with bad SQL
    print("\n--- Bad SQL Handling ---")
    try:
        eng = await srv._get_massive_provider()
        result = await eng.query_data("SELECT * FROM nonexistent_table")
        if isinstance(result, dict) and "error" in result:
            print(f"  OK Bad SQL returns error: {str(result['error'])[:80]}")
        else:
            print(f"  WARN Bad SQL didn't return error: {str(result)[:100]}")
    except Exception as e:
        print(f"  FAIL Bad SQL raised exception: {type(e).__name__}: {e}")


async def main():
    await test_alpaca_paper()
    await test_data_providers()
    await test_hidden_killers()
    await test_error_handling()
    
    print("\n" + "=" * 55)
    print("  AUDIT COMPLETE")
    print("=" * 55)


if __name__ == "__main__":
    asyncio.run(main())
