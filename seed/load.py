"""Entry point alias: python -m seed.load → delegates to load_seed."""
import asyncio

from seed.load_seed import main

if __name__ == "__main__":
    asyncio.run(main())
