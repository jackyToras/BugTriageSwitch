# Switchboard

**Bug triage, in the room.** Switchboard turns unstructured reports into a visible, reviewable workflow. A triager classifies and summarizes each report, then a reproducer prepares the smallest useful verification plan. Teammates can see the report status, handoffs, and activity timeline in the dashboard.

## Stack

- React + TypeScript + Vite dashboard
- Python 3.12 + FastAPI API
- PostgreSQL for reports and event history
- Redis + Celery for asynchronous demo processing and retries
- Docker Compose for local development
- GitHub Actions for CI
- Switch Console agents and a Switch room-history adapter for dashboard mirroring

## Run

Install Docker Desktop, copy `.env.example` to `.env`, then run:

```sh
docker compose up --build -d
```

Open <http://localhost:5174> for the dashboard and <http://localhost:8000/docs> for the API. Port 5173 is left available for your other project. Without Switch credentials, the dashboard runs in demo mode with deterministic example reports. Demo reproduction plans are clearly marked; they are not executed against your product code.

## Connect Switch

1. In Switch Console, connect your chat bridge and create the triage room with the native triager and reproducer agents.
2. Create a dedicated mirror agent in Switch Console and add it to the same room. Its credential can read and post as that agent, so keep the file private. The dashboard adapter only reads room history and posts it to the local API.
3. Copy `.env.example` to `.env` if you have not already. Set `SWITCH_CREDENTIALS_HOST_PATH` to the full path of the mirror agent's `credentials.json` file and `SWITCH_ROOM_ID` to the room UUID. Leave `SWITCH_BASE_URL` blank: the adapter reads the local relay endpoint from the credential file and rewrites its loopback hostname for Docker.
4. Start the adapter with `docker compose --profile switch up --build -d switch-adapter`. It polls Switch room history every five seconds. The history API omits message and thread IDs, so the adapter creates stable deduplication IDs from message content and attaches recognizable triager/reproducer replies to the newest report from that room.

Reports posted in the Switch room are handled by the real Switch agents. Reports created with the dashboard's **New report** form use the local Celery demo processor; its button is labeled **Run demo triage**. On Switch-originated reports, the dashboard points you back to the room to mention `@Triager` instead of running the demo processor.

Switch Console-managed agents receive events through their controller stream; their credentials cannot open a standalone agent event stream. The compact history route is the supported read path for this local mirror. Since it does not expose thread IDs, agent replies are associated with the newest report in the room, which is intended for the single-report-at-a-time triage demo.

Keep `.env` and the agent credential file local and private. Switch agent tokens do not have a read-only permission scope; the dedicated mirror identity limits its room membership, and the adapter implementation only calls event and heartbeat routes.

## Demo flow

1. Submit an issue in the dashboard or post a new root message in the connected Switch room.
2. Switchboard stores human Switch messages as reports in PostgreSQL; triager and reproducer replies appear in the newest report's activity timeline.
3. Native Switch agents triage the report and route bugs to the reproducer. Dashboard demo submissions instead enqueue a Celery job through Redis and use the deterministic demo processor.
4. Review the status and agent handoff in the dashboard.

Try: “After I change the account email, the profile page shows the new address but checkout still sends the receipt to the old one. Refreshing does not help.”

See `.env.example` for all configuration. The processor in `backend/app/tasks.py` is the demo agent seam for a real model or execution environment.
