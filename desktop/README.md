# BronchoRoute desktop shell

Dev run, from this directory:

```sh
npm install
npm start
```

The shell starts `pipeline/app_server.py` with `uv` (must be on `PATH`) and opens the viewer.
Set `BRONCHOROUTE_DATA_ROOT` to use a data root other than
`~/Library/Application Support/BronchoRoute` (a pipeline tree, for local dev).
