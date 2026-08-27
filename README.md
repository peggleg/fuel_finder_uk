# UK Fuel Finder for Home Assistant
[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/hacs/integration)
<a href="https://buymeacoffee.com/peggleg"><img src="https://cdn.buymeacoffee.com/buttons/v2/default-yellow.png" alt="Buy Me A Coffee" height="20"></a>

Track petrol and diesel prices in your Home Assistant setup using actual UK government data. Stop guessing which petrol station has the cheapest fuel nearby. This integration shows you exactly where to go.

Gets the cheapest petrol and diesel prices near you, updates every 15 minutes, and puts them in Home Assistant so you can build automations around them. Track more than one address (e.g. home and work) and each gets its own set of sensors. Tag a favourite station too, and compare its price against the cheapest one.

Fuel types tracked: E5 and E10 petrol, B7 and Super diesel.

## Before you start

You need a free developer account at https://www.developer.fuel-finder.service.gov.uk, an OAuth app (to get your Client ID and Secret), and the latitude and longitude of the location you want to track. Google Maps will give you the coordinates. Takes about 5 minutes to set up.

## Installing

**Via HACS (easier):** Open HACS, go to Integrations, click the menu and pick "Custom repositories". Paste in `https://github.com/peggleg/fuel_finder_uk` and pick "Integration". Search for "UK Fuel Finder" and install. Restart Home Assistant.

**Manual:** Download the `fuel_finder_uk` folder, copy it into `config/custom_components/`, and restart Home Assistant.

## Setting it up

Go to Settings → Devices & Services and create a new integration. Search for "UK Fuel Finder". Fill in:
- Location Name (e.g. "Home" or "Office", so you can tell locations apart)
- Client ID and Secret (from the developer portal)
- Latitude and longitude
- Search radius (defaults to 5 miles)
- Update interval in minutes (5-1440, defaults to 15)

Home Assistant checks your credentials and you're done. Sensors pop up automatically, grouped under a device named after your location.

### Tracking more than one location

Add the integration again from Settings → Devices & Services, and give it a different name and coordinates. Each location gets its own device card and its own full set of sensors, entirely independent of the others.

### Driving distance (optional)

By default, "distance" is straight-line ("as the crow flies"), the direct gap between two points, not how far you'd actually drive to get there. Roads bend, so the real driving distance is always a bit more.

If you want actual driving distance too, grab a free API key from [OpenRouteService](https://openrouteservice.org/dev/#/signup) (no credit card needed) and paste it into the "OpenRouteService API Key" field during setup, or add it later by editing the integration. Once it's set, stations get a `distance_driving` attribute alongside the existing straight-line `distance`, and the nearby stations list sorts by driving distance instead.

This is entirely optional. Skip it and everything works exactly as before, just with straight-line distance only. Driving distances are cached for a day at a time, since roads don't move, so this barely touches your API quota even on a free account.

### Favourite station

Pick a station you already use as your favourite, and get its prices right next to the cheapest one. No maths involved, just compare and decide.

Gives you a "Favourite Station" dropdown plus four price sensors (E10, E5, diesel, super diesel) for whichever station you pick, same shape as the cheapest-price sensors.

The dropdown only lists stations that were near you when you first set up the integration, kept separate from your live search radius. Narrowing that radius later won't drop your favourite. Widening it won't add new options to the dropdown either. To pick from a different set of stations, remove and re-add the integration.

Leave it on "None" and the four favourite sensors just won't show anything.

## What you get

Price sensors for E10, E5, diesel, and super diesel, updated every 15 minutes. Each shows the cheapest price in your area and which station has it. Click into a sensor and you'll see the station name, brand, address, coordinates, straight-line distance, and driving distance (if you've set up an OpenRouteService key). Petrol sensors use a pump icon, diesel sensors use the outline version, so they're easy to tell apart at a glance.

You also get a sensor for how many stations are nearby, with an attribute listing each one (name, brand, distance, postcode, and which fuel types it sells), plus a sensor for when the data was last updated.

Favourite a station and you'll get its E10, E5, diesel, and super diesel prices as their own sensors too.

Not every station sells super diesel. If none of your nearby stations do, that sensor will show as unavailable rather than a price.

## Examples

**Dashboard card to show prices:**
Fuel Type: Price, Station, Postcode, Distance

```yaml
**Fuel Prices Right Now**

**E10:** {{ states('sensor.uk_fuel_finder_home_cheapest_petrol_e10_price') }}p at {{ state_attr('sensor.uk_fuel_finder_home_cheapest_petrol_e10_price', 'station_name')|title }}, {{ state_attr('sensor.uk_fuel_finder_home_cheapest_petrol_e10_price', 'postcode') }} — {{ state_attr('sensor.uk_fuel_finder_home_cheapest_petrol_e10_price', 'distance_driving') }}m away

**E5:** {{ states('sensor.uk_fuel_finder_home_cheapest_petrol_e5_price') }}p at {{ state_attr('sensor.uk_fuel_finder_home_cheapest_petrol_e5_price', 'station_name')|title }}, {{ state_attr('sensor.uk_fuel_finder_home_cheapest_petrol_e5_price', 'postcode') }} — {{ state_attr('sensor.uk_fuel_finder_home_cheapest_petrol_e5_price', 'distance_driving') }}m away

**Diesel:** {{ states('sensor.uk_fuel_finder_home_cheapest_diesel_price') }}p at {{ state_attr('sensor.uk_fuel_finder_home_cheapest_diesel_price', 'station_name')|title }}, {{ state_attr('sensor.uk_fuel_finder_home_cheapest_diesel_price', 'postcode') }} — {{ state_attr('sensor.uk_fuel_finder_home_cheapest_diesel_price', 'distance_driving') }}m away

**Super Diesel:** {{ states('sensor.uk_fuel_finder_home_cheapest_super_diesel_price') }}p at {{ state_attr('sensor.uk_fuel_finder_home_cheapest_super_diesel_price', 'station_name')|title }}, {{ state_attr('sensor.uk_fuel_finder_home_cheapest_super_diesel_price', 'postcode') }} — {{ state_attr('sensor.uk_fuel_finder_home_cheapest_super_diesel_price', 'distance_driving') }}m away

**Updated:** {{ as_timestamp(states('sensor.uk_fuel_finder_home_last_update')) | timestamp_custom('%H:%M') }}
```

## Tweaking things

Want to change how often prices update? Re-open the integration settings (Settings → Devices & Services, find UK Fuel Finder, click the three dots → Edit) and change the update interval. You can set it anywhere from 5 to 1440 minutes (24 hours). Minimum 5 minutes due to API rate limits. Prices don't change faster than every 15 minutes anyway, so 15 is a good default.

## Troubleshooting

**Invalid OAuth credentials:** Double-check your Client ID and Secret match the developer portal. Make sure you created the OAuth app, not just the account. If you're hitting the API too much, you might get rate-limited temporarily.

**Timeout connecting to API:** Check your internet is working. The government's API might be down (rarely). Wait a minute and try again.

**No stations showing up:** Try a bigger search radius (10 miles instead of 5). Make sure your coordinates are right by checking them in Google Maps.

**Super diesel sensor shows unavailable:** Normal if none of your nearby stations sell it. It's a less common grade than standard diesel and petrol.

**Favourite station sensors show unavailable:** Either nothing's picked yet, or that station doesn't sell that fuel type.

**Prices look old:** Retail stations report prices with a 15-minute delay. The government data is only as fresh as what retailers submit.

## Privacy and data

This reads public fuel prices from the government's official API. No personal data goes anywhere. Prices aren't stored on disk, just kept in memory for the session.

## What won't work

Very remote areas might not have enough stations. Independent petrol stations sometimes don't update their prices. The API data lags about 15 minutes.

## Having problems?

Check the [GitHub issues](https://github.com/peggleg/fuel_finder_uk/issues) to see if someone's already hit the same problem. If not, create an issue with what you expected, what happened, your Home Assistant version, and any errors from the logs.

## Contributing

This is Apache 2.0 licensed so feel free to fork, modify, and improve it.
