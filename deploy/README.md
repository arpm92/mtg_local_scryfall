# Portainer deployment

This stack packages the collection and deck builder, and installs Codex CLI for the optional chat panel. The enriched collection and Scryfall cache are mounted read-only; saved decks and Codex's sign-in/session data persist in host directories.

## Prepare the host

1. Put a checkout of this repository on the home server (for example, `E:\repos\mtg`). Keep the collection, `.scryfall_cache`, `outputs`, and `.codex-home` on that host so Docker can bind-mount them.
2. Copy `.env.portainer.example` to a server-local `.env.portainer`. Set the Windows paths and the server's LAN address. If using Portainer's Git stack, enter the same values under **Environment variables**.
3. Confirm the enriched CSV and `.scryfall_cache\oracle_cards.jsonl.gz` exist, and that `outputs\commander`, `outputs\pauper`, `outputs\standard`, and `.codex-home` are writable by Docker Desktop. Create the Codex data directory before deploying: `New-Item -ItemType Directory -Force .codex-home`.

## Deploy in Portainer

Create a Stack using this repository's Git URL and `compose.yaml`, or run `docker compose --env-file .env.portainer up -d --build` from the checkout on the server. Portainer builds the image and starts the container. The app prompts for its configured login. The default URL for the documented server is `http://192.168.50.102:8015/`.

To update the app, pull/redeploy the stack with **Re-pull image and redeploy** enabled. Collection updates are separate: refresh/enrich the CSV on the host, then restart the stack if the CSV changed.

## Network boundary

The app has no web login and its API can create and delete decks. Keep it bound to the trusted home LAN and do not forward the port to the public internet. For remote access, use a private VPN or a protected HTTPS proxy and keep the app port unreachable directly from untrusted networks. Do not commit `.env.portainer` or `.codex-home`.

## Connect Codex chat

The chat uses the Codex app-server with the ChatGPT account signed into its server-side Codex home; it does not ask for an API key. The image includes Codex CLI. Sign-in is a one-time deployment step, separate from the browser UI. Open the running container's console in Portainer and use the supported Codex sign-in flow for a remote/headless host. The `CODEX_HOME` directory is persisted at `.codex-home`, so rebuilding retains the CLI's credential manager state. The web page reports when Codex is not connected. Never paste credentials into the chat panel or a browser field.

OpenAI documents ChatGPT-plan use through Codex app-server as a preview. A remote-host flow may require approved Sign in with ChatGPT credentials and a stable host ID. Follow OpenAI's [self-hosted VM instructions](https://developers.openai.com/siwc/token-sharing-open-source/self-hosted-vms) if the CLI does not offer a supported remote sign-in. Do not export API keys or use unofficial ChatGPT endpoints. Underlying requests use OpenAI's Responses endpoint with your ChatGPT-plan credentials; they are not API-key billing, and remain subject to plan eligibility, usage limits, and the preview constraints. This app starts each turn in a read-only sandbox and gives it only deck and owned-card context; it cannot save deck changes. Chat suggestions must be applied in the editor and pass the shared legality/ownership validator.

## Forge

Forge is not included in this container. The current Forge runner expects the Windows installation and Java on the host. The server has enough CPU/RAM to try one headless simulation at a time, but it shares 16 GB RAM with Windows and existing containers. Start with the existing 2 GB Forge heap, monitor host memory and CPU, and avoid running Forge games concurrently with other heavy services. Multi-player Commander performance still needs measurement on the server.
