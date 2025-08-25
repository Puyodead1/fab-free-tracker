import json
import re
import time
import tomllib
from datetime import datetime, timezone
from pathlib import Path

import requests
import zendriver as zd
from bs4 import BeautifulSoup
from discord import Embed
from discord.webhook import SyncWebhook
from markdownify import markdownify as md

cache = {}
config = {}

CACHE_PATH = Path("cache.json")
CONFIG_PATH = Path("config.toml")
BASE_URL = "https://www.fab.com/"
ICON_URL = "https://static.fab.com/static/builds/web/dist/frontend/assets/images/common/favicon/55950a2ddbbaa3937c5a48b0bd460965-v1.png"

if not CONFIG_PATH.exists():
    raise FileNotFoundError("config.toml not found")

with CONFIG_PATH.open("rb") as f:
    config = tomllib.load(f)

if CACHE_PATH.exists():
    with CACHE_PATH.open("r") as f:
        cache = json.load(f)
        print("Cache Loaded")


discord_session = requests.Session()
webhook = SyncWebhook.from_url(url=config["webhook_url"], session=discord_session)


def save_cache():
    with open(CACHE_PATH, "w") as f:
        json.dump(cache, f, indent=4)


async def get_listings():
    browser = await zd.start(headless=False)
    page = await browser.get(BASE_URL)
    await page.wait_for_ready_state("complete")
    # await page.wait_for(selector="#js-json-data-prefetched-data", timeout=15)
    script_element = await page.find("#js-json-data-prefetched-data", best_match=True)

    script_html = await script_element.get_html()
    await browser.stop()
    match = re.search(r"<script[^>]*>(.*?)</script>", script_html, re.DOTALL)
    if match:
        script_content = match.group(1).strip()

        data = json.loads(script_content)
    else:
        raise Exception("Could not extract script content")

    blades = data["/i/layouts/homepage"]["blades"]
    # find blade with title starting with Limited-Time Free
    blade = next(blade for blade in blades if blade["title"].startswith("Limited-Time Free"))
    if not blade:
        raise Exception("Could not find Limited Time Free blade")
    listings = blade["tiles"]

    def extract_listings(title):
        # extract the listing property
        return title["listing"]

    return list(map(extract_listings, listings))


def send_to_discord(listing):
    uid = listing["uid"]
    title = listing["title"]
    description = md(listing["description"])
    listing_type = listing["listingType"]
    price = listing["startingPrice"]["price"]
    discounted_price = listing["startingPrice"]["discountedPrice"]
    discount_start_date = datetime.fromisoformat(listing["startingPrice"]["discountStartDate"])
    discount_end_date = datetime.fromisoformat(listing["startingPrice"]["discountEndDate"])
    seller_name = listing["user"]["sellerName"]
    seller_avatar = listing["user"]["profileImageUrl"]
    thumbnail = listing["thumbnails"][0]["mediaUrl"]

    title = title[: 256 - len(title) - 3] + "..." if len(title) > 256 - len(title) else title
    description = description[:4093] + "..." if len(description) > 4096 else description

    # capitalize the first letter of the listing type
    listing_type = listing_type.capitalize()

    embed = Embed(title=title, description=description, color=0x00FF00, url=f"{BASE_URL}listings/{uid}")
    embed.set_author(name=seller_name, icon_url=seller_avatar)
    embed.add_field(name="Price", value=f"~~${price}~~ ${discounted_price}", inline=True)
    embed.add_field(
        name="Discount Starts",
        value=f"<t:{int(discount_start_date.timestamp())}:R> ({discount_start_date.strftime('%m/%d/%Y')})",
        inline=True,
    )
    embed.add_field(
        name="Discount Ends",
        value=f"<t:{int(discount_end_date.timestamp())}:R> ({discount_end_date.strftime('%m/%d/%Y')})",
        inline=True,
    )
    embed.set_thumbnail(url=thumbnail)
    embed.set_footer(text="Fab Tracker by Puyodead1", icon_url=ICON_URL)

    print("Sending new listing " + uid)
    msg = webhook.send(embed=embed, username="Fab Tracker", avatar_url=ICON_URL, wait=True)
    cache.update({uid: {"msg_id": msg.id, **listing}})
    save_cache()


async def main():
    try:
        listings = await get_listings()
        for listing in listings:

            uid = listing["uid"]
            discount_end_date = datetime.fromisoformat(listing["startingPrice"]["discountEndDate"])

            r = cache.get(listing["uid"])
            if r:
                # check if the discount has ended
                if discount_end_date < datetime.now(timezone.utc):
                    try:
                        print("Removing ended discount listing " + uid)
                        msg_id = r["msg_id"]
                        msg = webhook.fetch_message(msg_id)
                        msg.delete()
                        cache[uid] = {"msg_id": None, **listing}
                        save_cache()
                    except Exception as e:
                        print(f"Failed to delete {uid}: {e}")
                else:
                    print("Skipping existing listing " + uid)
            else:
                send_to_discord(listing)
                time.sleep(1)

    except Exception as e:
        raise e
