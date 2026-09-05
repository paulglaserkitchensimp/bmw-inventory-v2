# ── NOT USED by the 2026 330i hunt ───────────────────────────────────────────
# Legacy dealer-discovery / debugging script, superseded by platform_census.py
# + build_master_dealers.py. Kept for reference; nothing in the 330i pipeline
# calls it. See docs/330I_DEAL_FINDER.md § "What was disabled".
# ─────────────────────────────────────────────────────────────────────────────
import requests
import json

URL = (
    "https://v3zovi2qfz-dsn.algolia.net/1/indexes/*/queries"
    "?x-algolia-agent=Algolia%20for%20JavaScript%20(4.9.1)%3B%20Browser%20(lite)%3B%20JS%20Helper%20(3.22.4)"
    "&x-algolia-api-key=ec7553dd56e6d4c8bb447a0240e7aab3"
    "&x-algolia-application-id=V3ZOVI2QFZ"
)

HEADERS = {
    "Accept": "*/*",
    "Accept-Encoding": "gzip, deflate, br, zstd",
    "Accept-Language": "en-US,en;q=0.9",
    "Connection": "keep-alive",
    "DNT": "1",
    "Host": "v3zovi2qfz-dsn.algolia.net",
    "Origin": "https://www.bmwstore.com",
    "Referer": "https://www.bmwstore.com/",
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "cross-site",
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/145.0.0.0 Safari/537.36 Edg/145.0.0.0"
    ),
    "content-type": "application/x-www-form-urlencoded",
    "sec-ch-ua": '"Not:A-Brand";v="99", "Microsoft Edge";v="145", "Chromium";v="145"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"macOS"',
}

BODY = {
    "requests": [
        {
            "indexName": "thebmwstorecincinnati-sbm1224_production_inventory_mileage_high_to_low",
            "params": (
                "facetFilters=%5B%5B%22loaner_pages%3Aloaner%22%2C%22loaner_pages%3Aretired%22%5D"
                "%2C%5B%22make%3ABMW%22%2C%22make%3AMINI%22%5D"
                "%2C%5B%22type%3ACertified%20Pre-Owned%22%2C%22type%3APre-Owned%22%5D%5D"
                "&facets=%5B%22Location%22%2C%22algolia_sort_order%22%2C%22api_id%22%2C%22bedtype%22"
                "%2C%22body%22%2C%22certified%22%2C%22city_mpg%22%2C%22custom_sort_images%22"
                "%2C%22cylinders%22%2C%22date_in_stock%22%2C%22date_modified%22%2C%22days_in_stock%22"
                "%2C%22doors%22%2C%22drivetrain%22%2C%22engine_description%22%2C%22ext_color%22"
                "%2C%22ext_color_generic%22%2C%22ext_options%22%2C%22features%22%2C%22features%22"
                "%2C%22finance_details%22%2C%22ford_SpecialVehicle%22%2C%22fuel_efficient%22"
                "%2C%22fueltype%22%2C%22hash%22%2C%22hw_mpg%22%2C%22in_transit_filter%22"
                "%2C%22in_transit_sort%22%2C%22int_color%22%2C%22int_options%22%2C%22intransit_filter%22"
                "%2C%22lease_details%22%2C%22lightning%22%2C%22lightning.class%22"
                "%2C%22lightning.finance_monthly_payment%22%2C%22lightning.isPolice%22"
                "%2C%22lightning.isSpecial%22%2C%22lightning.lease_monthly_payment%22"
                "%2C%22lightning.locations%22%2C%22lightning.locations.meta_location%22"
                "%2C%22lightning.status%22%2C%22link%22%2C%22loaner_pages%22%2C%22location%22"
                "%2C%22make%22%2C%22metal_flags%22%2C%22miles%22%2C%22model%22%2C%22model_number%22"
                "%2C%22msrp%22%2C%22objectID%22%2C%22our_price%22%2C%22our_price_label%22"
                "%2C%22special_field_14%22%2C%22special_field_15%22%2C%22special_field_7%22"
                "%2C%22stock%22%2C%22thumbnail%22%2C%22title_vrp%22%2C%22transmission_description%22"
                "%2C%22trim%22%2C%22type%22%2C%22vin%22%2C%22year%22%5D"
                "&hitsPerPage=20&maxValuesPerFacet=250"
            ),
        },
        {
            "indexName": "thebmwstorecincinnati-sbm1224_production_inventory_mileage_high_to_low",
            "params": (
                "analytics=false&clickAnalytics=false"
                "&facetFilters=%5B%5B%22make%3ABMW%22%2C%22make%3AMINI%22%5D"
                "%2C%5B%22type%3ACertified%20Pre-Owned%22%2C%22type%3APre-Owned%22%5D%5D"
                "&facets=loaner_pages&hitsPerPage=0&maxValuesPerFacet=250&page=0"
            ),
        },
        {
            "indexName": "thebmwstorecincinnati-sbm1224_production_inventory_mileage_high_to_low",
            "params": (
                "analytics=false&clickAnalytics=false"
                "&facetFilters=%5B%5B%22loaner_pages%3Aloaner%22%2C%22loaner_pages%3Aretired%22%5D"
                "%2C%5B%22type%3ACertified%20Pre-Owned%22%2C%22type%3APre-Owned%22%5D%5D"
                "&facets=make&hitsPerPage=0&maxValuesPerFacet=250&page=0"
            ),
        },
        {
            "indexName": "thebmwstorecincinnati-sbm1224_production_inventory_mileage_high_to_low",
            "params": (
                "analytics=false&clickAnalytics=false"
                "&facetFilters=%5B%5B%22loaner_pages%3Aloaner%22%2C%22loaner_pages%3Aretired%22%5D"
                "%2C%5B%22make%3ABMW%22%2C%22make%3AMINI%22%5D%5D"
                "&facets=type&hitsPerPage=0&maxValuesPerFacet=250&page=0"
            ),
        },
    ]
}


def main():
    response = requests.post(
        URL,
        headers=HEADERS,
        data=json.dumps(BODY),
    )

    print(f"Status: {response.status_code}")
    data = response.json()
    print(json.dumps(data, indent=2))


if __name__ == "__main__":
    main()
