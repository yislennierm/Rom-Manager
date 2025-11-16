# ROMs Manager Admin UI

This folder hosts the web admin panel that FastAPI serves at `/admin`. The UI is built with React, Vite, TypeScript, and Ant Design.

## Commands

```bash
# Install dependencies
default$ npm install

# Start the Vite dev server on http://localhost:5173
npm run dev

# Type-check and create the production build consumed by FastAPI
npm run build
```

After running `npm run build`, FastAPI picks up the contents of `backend/ui/dist`. Start the backend as usual (e.g. `uvicorn backend.app.main:app --reload`) and visit `http://127.0.0.1:8000/admin` to load the compiled Ant Design UI.

The current placeholder fetches metadata from `/update/meta?target=modules|providers` and presents it using Ant Design cards. Extend `src/App.tsx` with additional routes/components as more admin workflows come online.
