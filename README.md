# BronchoRoute

BronchoRoute is a local macOS application. It turns a chest CT Archive into an
airway model and simulated bronchoscopy routes.

It is a research and education tool. It is not a medical device and it is not
for diagnosis or treatment decisions.

CT data and the processed Cases stay on the Mac. The application does not
upload them.

## Requirements

- Apple Silicon
- macOS 14 or later
- 16 GB of memory recommended

## Run from source

Install uv and Node.js. From this repository:

```sh
cd pipeline
uv sync
cd ../desktop
npm install
npm start
```

`uv sync` uses `pipeline/.python-version` (Python 3.12). `npm start` opens the
desktop shell, which starts the Python sidecar. The first segmentation
downloads model weights onto the Mac.

## Licence

BronchoRoute is GPL-3.0-or-later. See `LICENSE`, `COPYING.md`, and
`THIRD_PARTY_NOTICES.md`.
