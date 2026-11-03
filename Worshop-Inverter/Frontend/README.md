# Inverter Load Manager

React (Next.js), Python FastAPI and SQLite. Capacity: 800W.

## Run Locally

From this frontend folder:

```powershell
npm install
npm run dev
```

Open the address printed by Next.js, normally http://localhost:3000.
The API permits localhost and 127.0.0.1 on frontend ports 3000 and 3001.

In another terminal, go to the sibling API folder:

```powershell
cd ..\..\API
.\.venv\Scripts\python.exe -m pip install "fastapi[standard]"
.\.venv\Scripts\python.exe -m uvicorn main:app --port 8001 --reload
```

If the virtual environment does not exist, run `py -m venv .venv` first.
Interactive API documentation: http://127.0.0.1:8001/docs.

## Appliance States

- `off`: the user does not want this appliance running.
- `running`: the user wants it on and it has capacity.
- `shed`: the user wants it on, but the system paused it to make room.

New appliances start off. Wattage must be positive; priority is a whole number
starting at 1. Smaller priority numbers are more important. Wattage and priority
cannot be edited. An appliance larger than 800W can be registered but cannot run.
On/Off controls change the stored simulation, not physical equipment.

## Shedding and Restoring

When an On request needs room, check running appliances with strictly larger
priority numbers. Shed the least important first (largest priority number).
For ties, use registration order. Plan the complete change before saving it.
If these candidates cannot free enough capacity, return 409 with a reason and
leave every appliance exactly unchanged. Equal or higher priorities are protected.

After Off or Delete, try shed appliances in priority order (smallest number first).
For equal priorities, try the oldest registration first. Skip anything that does
not fit and continue trying the remaining appliances. This respects importance
while using spare capacity instead of leaving it idle behind an oversized item.
The tradeoff is that a large, important appliance might wait while smaller,
less-important appliances run. Restoration never sheds another appliance.
Explicitly turning a shed appliance off removes it from the waiting list.

## Persistence and Safety

The readable creation and migration code is in the backend's main module.
SQLite stores every appliance and its exact `state` in `API/appliances.db`.
Existing on/off records are migrated without deleting them. If legacy running
load already exceeds 800W, the migration sheds the least important appliances.
Once migrated, restart does not recalculate or reset states.

Each write uses `BEGIN IMMEDIATE`: only one load-changing request can decide
and save at a time, including requests from different processes. Shed updates
happen before the requested appliance starts. Failure rolls back the transaction.
Decimal arithmetic avoids floating-point rounding around the 800W boundary.

The frontend uses the backend's full snapshot, never its own load calculation.
It refreshes after changes and polls every three seconds while idle. Failed
refreshes show a warning, and old reads are cancelled during a write.
Action messages explain shedding, restoration and rejected requests.

## Endpoints

- `POST /api/applianceForm`: register `{name, wattage, priority}`; returns 201.
- `GET /api/state`: appliances, capacity, current load and remaining capacity.
- `GET /api/appliances`: appliance list for compatibility.
- `POST /api/appliances/{id}/on`: request On; returns 409 if it cannot fit.
- `POST /api/appliances/{id}/off`: request Off, including cancellation of shed.
- `POST /api/appliances/{id}/toggle`: compatibility route; shed toggles to off.
- `DELETE /api/appliances/{id}`: delete and attempt restoration.

Missing IDs return 404. Invalid input returns 422. Successful mutations return
the full picture plus a human-readable message. No login or event log is included.

## Checks

From the API folder:

```powershell
.\.venv\Scripts\python.exe -m unittest test_main -v
```

Tests use temporary databases, not saved user appliances. They cover the PDF
walkthrough, rejection rollback, restart persistence, restore order, explicit
Off, deletion, migration, fractional watts and simultaneous requests.

From the frontend folder: `npx eslint app/page.js`.
