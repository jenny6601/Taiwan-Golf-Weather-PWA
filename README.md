# Taiwan Golf Weather PWA

Taiwan Golf Weather V32.2 Progressive Web App.

## Application files
- `index.html`: app entry point
- `manifest.json`: install metadata and icons
- `sw.js`: app-shell cache and offline fallback
- `offline.html`: offline fallback page
- `pwa/pwa-install.js`: Service Worker registration
- `icons/`: Android and iPhone app icons

## Live weather data
The PWA interface is hosted independently here. To avoid duplicating the large, automatically refreshed weather datasets, it reads the public JSON files from the original `Taiwan-Golf-Weather` repository's `main` branch via raw.githubusercontent.com:
- `data/live_observations.json`
- `data/forecast_cache.json`

The original repository's existing GitHub Actions continue maintaining these datasets. This PWA repository does not run or modify those collectors.

## GitHub Pages
After validation, configure Pages to **Deploy from a branch**, branch `main`, folder `/(root)`.
Expected URL: https://jenny6601.github.io/Taiwan-Golf-Weather-PWA/

## Safety boundary
This repository is separate from `Taiwan-Golf-Weather`. Do not change the original repository's production `main`, V31.6 site, or weather-data workflows as part of PWA deployment.

## Forecast source extension (six models + Visual Crossing)

- The nationwide forecast cache now includes six Open-Meteo model series: ECMWF IFS, NOAA GFS, DWD ICON, JMA GSM, Canada GEM, and ECMWF AIFS Single.
- AIFS uses model ID \`ecmwf_aifs025_single\`. Its native output is 6-hourly; Open-Meteo interpolates it to an hourly series for display. AIFS Single does not provide native precipitation probability, wind gusts, or UV index; those values remain null rather than being fabricated.
- The existing five models continue their hourly refresh. AIFS is refreshed approximately every 5.5 hours to align with its six-hour model cycle and avoid redundant API requests.
- Visual Crossing Timeline API collects 15-day hourly forecasts for all 57 courses through \`.github/workflows/visual-crossing-hourly-pilot.yml\`. The workflow publishes per-course JSON to the \`visual-crossing-cache\` branch.
- The Visual Crossing API key is stored as the GitHub Actions secret \`VISUAL_CROSSING_API_KEY\`; it is never exposed in the browser.
- Visual Crossing is displayed as an independent external comparison and is not included in the six-model median until historical verification supports that change.
