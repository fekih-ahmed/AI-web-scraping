import asyncio
import argparse
import sys

from config import Config
from logger import setup_logger
from scraper import ASOSScraper


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="ASOS Women's Jeans Scraper - Production-grade Playwright scraper"
    )
    parser.add_argument(
        "--url",
        type=str,
        default=None,
        help="Target ASOS category URL",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        default=True,
        help="Run browser in headless mode (default: True)",
    )
    parser.add_argument(
        "--no-headless",
        action="store_false",
        dest="headless",
        help="Run browser in visible mode",
    )
    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="Output JSON file path",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Maximum number of products to scrape",
    )
    parser.add_argument(
        "--proxy",
        type=str,
        default=None,
        help="Proxy URL (e.g., http://user:pass@host:port)",
    )
    parser.add_argument(
        "--max-concurrent",
        type=int,
        default=5,
        help="Maximum concurrent product page scrapes (default: 5)",
    )
    parser.add_argument(
        "--slow-mo",
        type=int,
        default=50,
        help="Slow motion delay in ms (default: 50)",
    )
    return parser.parse_args()


async def main() -> None:
    args = parse_args()

    config = Config(
        headless=args.headless,
        page_limit=args.limit,
        max_concurrent_pages=args.max_concurrent,
        slow_mo=args.slow_mo,
    )

    if args.url:
        config.target_url = args.url
    if args.output:
        config.output_file = args.output
    if args.proxy:
        config.proxy = args.proxy

    logger = setup_logger()
    logger.info("=" * 60)
    logger.info("ASOS Jeans Scraper - Starting")
    logger.info(f"Target: {config.target_url}")
    logger.info(f"Headless: {config.headless}")
    logger.info(f"Output: {config.output_file}")
    logger.info("=" * 60)

    scraper = ASOSScraper(config)

    try:
        products = await scraper.scrape()
        logger.info(f"\nSuccessfully scraped {len(products)} products")
        logger.info(f"Results saved to: {config.output_file}")

        if not products:
            logger.warning("No products were scraped. Check the target URL and try again.")

    except KeyboardInterrupt:
        logger.info("\nScraping interrupted by user")
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
