# Tara

**The self-hosted, simple, AI-assisted inventory tool.**

Spreadsheet-style tables organised in folders, items catalogued from photos by an AI of your choice,
and search that understands meaning. Built for SigmaCamp Brasil's boxes of camp materials, but works
for any physical inventory.

- **Tables in nested folders**: sidebar tree, spreadsheet-like tabs, inline editing, custom columns
  (text, number, date, checkbox, choice list, link). Quantities can be exact or estimated (`~75`, `3 rolls`, `1.2 kg`).
- **Photos**: per row; click to open a zoomable viewer; drag files onto a row to attach.
- **Add with AI**: upload photos (or take them on a phone), add an optional note, and review the proposed
  rows. For each row you can accept it, edit it, or ask the AI to change something ("split by colour",
  "write in Portuguese") for the whole draft or for one row. Works with Claude, ChatGPT, Gemini, Ollama, or any
  OpenAI-compatible server.
- **Guided add**: a focused, phone-friendly loop for cataloguing box after box. Start it from any folder,
  then browse, create or pick a table, snap photos, confirm the proposed items, and repeat. There's no grid
  in the way, and you can type an item in when there's nothing to photograph.
- **Ask AI** (`Ctrl/⌘+J`): a chat assistant that sees what's on screen (the open table, selected rows, the
  sidebar selection), searches and reads the inventory with tools, and opens and highlights tables for you.
  It answers in your language with clickable links to items. It's read-only.
- **Global search** (`Ctrl/⌘+K`): fuzzy text (partial words, typos) and semantic embeddings
  (meaning and PT⇄EN). You can scope it to any set of folders (subfolders included, individual children excluded) or to a single table.
- **Who and when**: every item records who added it and when, shown in an optional grid column, in search
  results and in exports. Times follow the timezone set in Settings.
- **Last updated** is shown on every table (header chip, tabs, tree, folder view, exports). Tables not
  updated for a long time are flagged.
- **Bulk actions**: multi-select rows or tree nodes to delete, move or archive them. Deletions go to a **Trash** and can be undone.
- **Archive** tables or folders, for example last year's inventory. Archived ones become read-only and are hidden from search.
- **Import/export** Excel (with embedded photos) and CSV. **Backup/restore** is a single zip.
- **Invite links** that work a set number of times (default 1, optional expiry). **AI token usage** is tracked per user and feature.
- Users with admin, editor and viewer roles; branding (name, colour, logo, favicon, theme); light and dark mode; responsive down to phones.

## Run with Docker

```bash
cp .env.example .env            # set SECRET_KEY
docker compose up -d --build
open http://localhost:8000      # first visit creates the admin account
```

All data (SQLite database, photos, branding) lives in the `tara-data` volume at `/data`.
Back it up from **Settings → Server & data → Download backup**, or copy the volume.

To import an existing workbook such as `Rio Inventory.xlsx`, use the sidebar's spreadsheet icon or a folder's
**Import XLSX**. Every sheet becomes a table. Header rows are detected automatically, title lines
("Summary: …", "Location: …") become the table's summary or AI context, and embedded photos are attached to their row.

### AI providers

Configure under **Settings → AI assistant**, then use **Test connection**.

| Provider | Notes |
|---|---|
| Anthropic Claude | Default `claude-opus-5-5`; uses structured outputs, adjustable effort, and optional server-side refusal fallback |
| OpenAI | `gpt-5` etc. with strict JSON schema |
| Google Gemini | `gemini-2.5-flash` etc. |
| Ollama | Any vision model (`qwen2.5vl`, `qwen3.6`, `llama3.2-vision`, `gemma3`). Base URL e.g. `http://host.docker.internal:11434` |
| OpenAI-compatible | OpenRouter, Groq, Mistral, LM Studio, vLLM… (falls back to plain JSON mode if the server lacks JSON schema) |

API keys are stored encrypted with a key derived from `SECRET_KEY`. **Keep `SECRET_KEY` stable**: if it
changes, saved API keys can no longer be decrypted and must be entered again.

### Search

Search is fuzzy text (SQLite FTS5 trigram) merged with semantic embeddings using reciprocal rank fusion.
By default the embeddings come from a local multilingual model (`paraphrase-multilingual-MiniLM-L12-v2`)
that is baked into the image, so search works offline. Under **Settings → Search** you can switch to
OpenAI, Gemini or Ollama embeddings, check index status, and rebuild the index.

## Development (NixOS / direnv)

```bash
direnv allow                                   # shell.nix: python 3.13, uv, libstdc++
uv sync
DATA_DIR=.devdata uv run flask --app app db upgrade
DATA_DIR=.devdata uv run flask --app app run --debug --port 5055
uv run pytest                                   # unit + API tests
uv run pytest -m semantic                       # search quality on the real Rio inventory (downloads the model once)
```

After changing models: `DATA_DIR=.devdata uv run flask --app app db migrate -m "..."`.

Browser tests (Playwright in Docker). `e2e/devserver.sh` starts a fresh server on :5055:

```bash
./e2e/devserver.sh &
# UI smoke test (no AI needed), then the real-AI flow against an Ollama server:
docker run --rm --network host --user $(id -u):$(id -g) -e HOME=/tmp -v $PWD:/w -w /w \
  mcr.microsoft.com/playwright/python:v1.55.0-noble \
  sh -c "pip -q install --user playwright==1.55.0 && python e2e/smoke.py http://127.0.0.1:5055"
# ... python e2e/ai_ollama.py http://127.0.0.1:5055 http://OLLAMA_HOST:11434 qwen3.6:latest
```

For Ollama reasoning models (e.g. qwen3.6), leave "Let reasoning models think first" off in Settings. Thinking
takes 10–20× longer, and the long reasoning can use up the output budget.

### Layout

```
app/
  models.py, quantity.py          data model; quantity parsing/formatting
  services/                       inventory ops (soft delete, trash, archive), photos, XLSX, backup, settings
  search/                         FTS5 + embeddings, background indexer, hybrid query
  ai/                             prompt, per-table JSON schema, provider adapters, propose/refine/commit,
                                  chat assistant (tool loop + tools), token usage
  api/                            JSON REST endpoints
  static/js/                      app shell (Alpine), tree, Tabulator grid, palette, AI wizard, viewer
migrations/                       Alembic
tests/, e2e/                      pytest suite, Playwright smoke tests
```

## License

Tara is free software, licensed under the [GNU General Public License v3.0 or later](LICENSE).
