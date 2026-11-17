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
