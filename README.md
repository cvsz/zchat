# Camfrog standalone feature apps

This document is also available in Thai: `README-TH.md`.

Full user manual: [`docs/USER-MANUAL-EN.md`](docs/USER-MANUAL-EN.md) (Thai: [`docs/USER-MANUAL-TH.md`](docs/USER-MANUAL-TH.md)).

- English: [`README-EN.md`](README-EN.md)
- ไทย: [`README-TH.md`](README-TH.md)

Windows only. Selector-based standalone apps discover Camfrog controls on startup and save valid selector proposals automatically. Use **Setup → Auto-detect** or **Discover controls** to retry; Web Status uses website controls and does not discover Camfrog controls.

## ZChat Control Server

The separate ZChat Control Server local MVP, including its local PostgreSQL setup, agent workflow, dashboard, and verified integration limits, is documented in [`control-server/README.md`](control-server/README.md). It binds to loopback for local development and is not a production deployment.

The Windows agent chat client for the internal control-server protocol is documented in [`zchat-client/README.md`](zchat-client/README.md). It is a separate executable and does not connect to Camfrog's network protocol.
