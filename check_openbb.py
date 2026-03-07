
try:
    from openbb import obb
    print("OpenBB Version:", obb.package_version if hasattr(obb, 'package_version') else "Unknown")
    
    print("\nChecking available providers for news.world...")
    try:
        # Just use symbol news as it's more direct for specific tickers
        obb.news.world(symbol="AAPL", provider="check")
    except Exception as e:
        print(f"Provider info from news.world: {e}")
        
    try:
        # news.company might be better for specific stocks
        obb.news.company(symbol="AAPL", provider="check")
    except Exception as e:
        print(f"Provider info from news.company: {e}")

except ImportError:
    print("OpenBB not installed.")
except Exception as e:
    print(f"Unexpected error: {e}")
