# Strategy Factory dashboard

React + TypeScript + Vite + Tailwind 4 + ECharts. Served by the FastAPI backend (`python -m sfactory.web`).

```
npm install
npm run build     # writes dist/, which the backend serves at /
npm run dev       # http://localhost:5173, /api proxied to http://127.0.0.1:8765
```

Design tokens live in `src/index.css`: one fixed colour per edge family (MR, TF, VOL, XS, CAL, EV), green / red
for sign, one accent; light and dark palettes. See `docs/spec/web.md` for the pages and how to extend them.
