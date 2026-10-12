# Shared assistant API

GET /api/copilot/info reports the app origin and stable data-space identity. GET /api/copilot/launcher.js provides the shared launcher, while GET /api/copilot/panel.js and /api/copilot/proxy relay the globally configured Faustus service. Tokens stay on the server.

The editor context contains the native document and selected layer IDs, the canvas and complete edited canvas fields. New or missing documents have no active object. The context captures the visible selection and draft before requesting current native data. Turn refresh preserves newer typing and manual clears, updates untouched fields and rejects both pending loads if another document opens.

The manifest's existing generic REST MCP bridge remains configured with bridges/rest_mcp/manifests/platos.json. Its document/editor routes use /api/v2; the configured catalogue including app_status has12 tools. Editor commands retain the native base_revision contract. See the editor API and manifest for document, job and export operations. Faustus owns conversations and agent execution; this app adds no duplicate chat store or model loop.
