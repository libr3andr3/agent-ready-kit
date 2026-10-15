# agent-ready-kit

**Give any website a chat assistant, an MCP server and an A2A agent, all configured from one JSON file.**
One Python file, standard library only, no framework and no build step.

AI agents are becoming how people find and buy: they read websites, call tools and talk to other agents. Most
business websites can't answer them. This kit puts one small server next to your site so that:

- **people** get a chat assistant that knows your business (the widget in the corner of the page);
- **AI agents** (Claude, ChatGPT, or any MCP or A2A client) can look up your business, your services and your
  documents through standard protocols;
- **agents can discover all of that** through the usual cards in `/.well-known/`.

It was extracted from the assistant that answers on [yaya.tech](https://yaya.tech), built by Yaya Tech PBC, with
everything specific to Yaya Tech removed.

## What you get

| Endpoint | What it does |
|---|---|
| `POST /api/chat` | Backend for the chat widget. The model answers from your `site.json` facts only. |
| `GET /api/widget-config` | Widget texts and options (from the `widget` block of `site.json`). |
| `POST /mcp` | MCP server (Streamable HTTP, stateless, JSON responses; protocol 2025-11-25, 2025-06-18, 2025-03-26). Read-only tools, **no model involved**: `get_business_info`, `list_services`, `search`, `fetch` (the pair ChatGPT connectors require), `check_agent_readiness`. |
| `POST /a2a` | A2A 0.3.0 agent (`message/send`). The model, with read-only tools only. |
| `GET /.well-known/agent-card.json`, `/.well-known/mcp.json` | Discovery cards, generated from `site.json`, so there is one source of truth. |
| `POST /identity/challenge`, `/identity/register` | Optional agent identity: an agent proves it holds a key pair (ML-DSA-65 post-quantum recommended, Ed25519 accepted) and gets a bearer token. |
| `GET /agent-kit/chat.js`, `chat.css`, `icon.svg` | The widget, without dependencies. |

The website check (`check_agent_readiness`) scores any public site from 0 to 100 on what agents need: AI crawlers
allowed in `robots.txt`, content readable without JavaScript, schema.org data, title and description, sitemap,
`llms.txt`, and A2A/MCP/UCP endpoints. Turn it off with `"features": {"readiness_check": false}`.

## Quickstart

```bash
git clone https://github.com/libr3andr3/agent-ready-kit && cd agent-ready-kit
cp site.example.json site.json          # describe your business here
export LLM_API_KEY=sk-or-...            # OpenRouter by default; see "Models" below
SERVE_STATIC=1 python3 server/app.py    # serves the demo page in examples/
```

Open <http://127.0.0.1:8146/#chat>. Then try the agent side:

```bash
curl -s localhost:8146/.well-known/agent-card.json
curl -s localhost:8146/mcp -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"list_services","arguments":{}}}'
npx -y @modelcontextprotocol/inspector --cli http://localhost:8146/mcp --transport http --method tools/list
```

## Put it on your site

1. Add the widget to your pages:
   ```html
   <link rel="stylesheet" href="/agent-kit/chat.css">
   <script src="/agent-kit/chat.js" defer></script>
   ```
   Any element with `data-chat` opens it, and `data-chat-en="When are you open?"` opens it with that message. The
   language comes from `<html data-lang>` or `<html lang>`. Re-theme it by overriding the `--yc-*` CSS variables.
2. Route only the agent paths to the kit and keep serving your site as it is. See
   [`deploy/Caddyfile.example`](deploy/Caddyfile.example) and [`deploy/agent-ready-kit.service`](deploy/agent-ready-kit.service)
   (create the `agentkit` system user first). Set `TRUST_PROXY=1` behind a proxy.
3. Is your site on another host (Netlify, Shopify, Squarespace…)? Run the kit on a subdomain, load the widget from
   there with `data-endpoint="https://agent.example.com"`, and set `CHAT_ALLOWED_ORIGINS=https://www.example.com`.
   The discovery cards then live on that subdomain.
4. Tell agents where you are: add a "For AI agents" section to your `llms.txt` with the `/mcp` and `/a2a` URLs.

## Models

Any OpenAI-compatible chat endpoint works:

| Where | `LLM_BASE_URL` | Notes |
|---|---|---|
| OpenRouter (default) | `https://openrouter.ai/api/v1` | Requests ask providers not to train on your data (`data_collection: deny`). `LLM_PROVIDER_IGNORE` excludes providers. |
| Your own machine (Ollama, vLLM, llama.cpp) | e.g. `http://127.0.0.1:11434/v1` | No key needed. Conversations never leave your infrastructure. |
| Any other compatible API | its `/v1` URL | Set `LLM_API_KEY` and `LLM_MODEL`. |

The model needs tool calling for the website check and for saving leads; without it the chat still answers.

## Security model

- **Agents are read-only.** Anonymous agents can read your public facts and check a public website. Nothing they do
  stores data, books, or sends email. MCP tools run without a model, so there is no prompt to inject.
- **The A2A agent is boxed in:** read-only tools, no secrets in its context, and instructions to treat every incoming
  message and every fetched page as data. Only a registered agent's key fingerprint reaches the model, never the
  label it chose for itself.
- **SSRF guard on website checks:** only http/https on ports 80 and 443, every resolved IP must be public, every
  redirect is re-checked, 6 s timeout, 1.5 MB limit. The tests try 127.0.0.1, 169.254.169.254, `[::1]` and 10.0.0.1.
- **Limits:** 30 chat messages per IP per 10 minutes, a daily cap on model calls shared by chat and A2A, 60 agent
  calls per 10 minutes (300 for identified agents), 10 website checks per 10 minutes and a daily global cap, and body
  limits of 32 KB for agents and 60 KB for chat.
- **Leads only with consent:** `save_lead` refuses unless the visitor explicitly agreed, and asks for no more than
  name, email and company.
- **Honest by default:** the assistant uses only the facts in `site.json`, never invents prices, people or links, and
  never promises results or rankings.
- **Everything in `site.json` is public.** If you list prices there, any person or agent can read them. Leave
  `price` out and use `pricing_note` to explain how you quote.
- **Logs** go to stdout and never include message contents. If you redirect them to a file, cap its size.

## Configuration (`site.json`)

Any text field can be a plain string or a per-language object such as `{"en": "...", "es": "..."}`.

| Key | Meaning |
|---|---|
| `name`, `url` (required) | Business name and canonical URL (used in cards and identity). |
| `organization`, `description`, `icon_url`, `documentation_url` | Optional, used by the A2A card. |
| `languages` | e.g. `["en", "es"]`; the first one is the default. |
| `contact` | `email`, `booking_url`, `phone`: the only contact points the assistant may give. |
| `assistant` | `name`, `about` (the facts), `rules` (extra rules), `max_words` (default 70). |
| `services` | `id`, `name`, and optionally `description`, `timeline`, `price`, `note`. |
| `pricing_note` | How pricing works. |
| `docs` | Extra documents for `search`/`fetch` and for the assistant: `id`, `title`, `text`, `url`. |
| `widget` | `bubbleTitle`, `bubble`, `greeting`, `status`, `placeholder`, `legal`, `privacy_url`, `chips` (`[label, message]` pairs), `icon`. |
| `features` | `readiness_check`, `save_lead`, `identity` (all `true` by default). |

Environment variables are listed at the top of [`server/app.py`](server/app.py).

## Optional agent identity

An agent creates a key pair (`openssl genpkey -algorithm ML-DSA-65`, OpenSSL 3.5+), asks for a one-use challenge,
signs it and registers its public key. The key's fingerprint becomes its `agent_id`, and it gets a 30-day bearer token
with higher rate limits. No registry or third party is involved, and anonymous agents keep every public tool.
ML-DSA verification uses the system `openssl`; Ed25519 uses the `cryptography` package. If neither is available,
identity is simply off. `GET /identity` returns the full spec with copy-paste commands.

## Tests

```bash
python3 -m unittest discover -s tests -v
```

The tests start the real server on a free port next to a fake model, and cover discovery cards, MCP conformance
(version negotiation, notifications, batches, unknown tools), the SSRF guard, payload limits, the chat tool loop, consent
for leads, A2A and path traversal on static files.

## License

Apache-2.0. Copyright 2026 Yaya Tech PBC.
