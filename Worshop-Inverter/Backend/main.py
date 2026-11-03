import sqlite3
from contextlib import asynccontextmanager, closing
from decimal import Decimal
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ConfigDict, Field

DB_PATH = Path(__file__).resolve().parent / "appliances.db"
CAPACITY = Decimal("800")


def get_picture(connection):
    connection.row_factory = sqlite3.Row
    rows = connection.execute(
        "SELECT id, name, wattage, priority, state FROM appliances ORDER BY id"
    ).fetchall()
    appliances = [dict(row) for row in rows]
    current_load = Decimal("0")

    for appliance in appliances:
        appliance["is_on"] = appliance["state"] == "running"
        if appliance["state"] == "running":
            current_load += Decimal(str(appliance["wattage"]))

    return {
        "appliances": appliances,
        "capacity": float(CAPACITY),
        "current_load": float(current_load),
        "remaining_capacity": float(CAPACITY - current_load),
    }


def restore_appliances(connection):
    rows = connection.execute(
        "SELECT wattage FROM appliances WHERE state = 'running'"
    ).fetchall()
    current_load = sum((Decimal(str(row[0])) for row in rows), Decimal("0"))
    waiting = connection.execute(
        "SELECT id, name, wattage FROM appliances "
        "WHERE state = 'shed' ORDER BY priority, id"
    ).fetchall()
    messages = []

    for appliance_id, name, wattage in waiting:
        watts = Decimal(str(wattage))
        if current_load + watts <= CAPACITY:
            connection.execute(
                "UPDATE appliances SET state = 'running' WHERE id = ?",
                (appliance_id,),
            )
            current_load += watts
            messages.append(f"{name} restored because capacity is available.")

    return messages


@asynccontextmanager
async def lifespan(app: FastAPI):
    with closing(sqlite3.connect(DB_PATH, timeout=10)) as connection:
        with connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("""
                CREATE TABLE IF NOT EXISTS appliances (
                    id INTEGER PRIMARY KEY,
                    name TEXT NOT NULL,
                    wattage REAL NOT NULL CHECK (wattage > 0),
                    priority INTEGER NOT NULL,
                    state TEXT NOT NULL DEFAULT 'off'
                        CHECK (state IN ('running', 'off', 'shed'))
                )
            """)
            columns = connection.execute("PRAGMA table_info(appliances)").fetchall()
            column_names = [column[1] for column in columns]
            if "state" not in column_names:
                connection.execute(
                    "ALTER TABLE appliances ADD COLUMN state TEXT NOT NULL "
                    "DEFAULT 'off' CHECK (state IN ('running', 'off', 'shed'))"
                )
                if "is_on" in column_names:
                    connection.execute(
                        "UPDATE appliances SET state = 'running' WHERE is_on = 1"
                    )
                    running = connection.execute(
                        "SELECT id, wattage FROM appliances WHERE state = 'running' "
                        "ORDER BY priority DESC, id"
                    ).fetchall()
                    load = sum(
                        (Decimal(str(row[1])) for row in running), Decimal("0")
                    )
                    for appliance_id, wattage in running:
                        if load <= CAPACITY:
                            break
                        connection.execute(
                            "UPDATE appliances SET state = 'shed' WHERE id = ?",
                            (appliance_id,),
                        )
                        load -= Decimal(str(wattage))
    yield

app = FastAPI(lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:3001",
        "http://127.0.0.1:3001",
    ],
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["Content-Type"],
)


class ApplianceForm(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    name: str = Field(min_length=1)
    wattage: float = Field(gt=0, allow_inf_nan=False)
    priority: int = Field(strict=True, ge=1)


@app.post("/api/applianceForm", status_code=201)
def submit_appliance_form(appliance: ApplianceForm):
    with closing(sqlite3.connect(DB_PATH, timeout=10)) as connection:
        with connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                """
                INSERT INTO appliances (name, wattage, priority)
                VALUES (?, ?, ?)
                """,
                (appliance.name, appliance.wattage, appliance.priority),
            )
            appliance_id = cursor.lastrowid
            picture = get_picture(connection)

    picture["message"] = f"{appliance.name} registered. It is off."
    picture["appliance"] = next(
        item for item in picture["appliances"] if item["id"] == appliance_id
    )
    return picture

@app.get("/api/appliances")
def get_appliances():
    with closing(sqlite3.connect(DB_PATH, timeout=10)) as connection:
        return get_picture(connection)["appliances"]


@app.get("/api/state")
def get_state():
    with closing(sqlite3.connect(DB_PATH, timeout=10)) as connection:
        return get_picture(connection)


@app.post("/api/appliances/{appliance_id}/{action}")
def change_appliance(appliance_id: int, action: Literal["on", "off", "toggle"]):
    with closing(sqlite3.connect(DB_PATH, timeout=10)) as connection, connection:
        connection.execute("BEGIN IMMEDIATE")
        appliance = connection.execute(
            "SELECT name, wattage, priority, state FROM appliances WHERE id = ?",
            (appliance_id,),
        ).fetchone()

        if appliance is None:
            raise HTTPException(status_code=404, detail="Appliance not found")

        name, wattage, priority, state = appliance
        if action == "toggle":
            action = "on" if state == "off" else "off"

        messages = []
        if action == "off":
            connection.execute(
                "UPDATE appliances SET state = 'off' WHERE id = ?", (appliance_id,)
            )
            messages.append(f"{name} is off and will not restore automatically.")
            messages.extend(restore_appliances(connection))
        elif state == "running":
            messages.append(f"{name} is already running.")
        else:
            running = connection.execute(
                "SELECT wattage FROM appliances WHERE state = 'running'"
            ).fetchall()
            load = sum((Decimal(str(row[0])) for row in running), Decimal("0"))
            requested_watts = Decimal(str(wattage))
            candidates = connection.execute(
                "SELECT id, name, wattage FROM appliances "
                "WHERE state = 'running' AND priority > ? "
                "ORDER BY priority DESC, id",
                (priority,),
            ).fetchall()
            to_shed = []

            for candidate in candidates:
                if load + requested_watts <= CAPACITY:
                    break
                to_shed.append(candidate)
                load -= Decimal(str(candidate[2]))

            if load + requested_watts > CAPACITY:
                raise HTTPException(
                    status_code=409,
                    detail=f"Cannot turn on {name} ({wattage:g}W): even after "
                    "shedding all lower-priority running appliances, the load "
                    "would exceed 800W. Equal or higher priorities are protected. "
                    "Nothing was changed.",
                )

            for shed_id, shed_name, shed_watts in to_shed:
                connection.execute(
                    "UPDATE appliances SET state = 'shed' WHERE id = ?", (shed_id,)
                )
                messages.append(f"{shed_name} shed to make room for {name}.")

            connection.execute(
                "UPDATE appliances SET state = 'running' WHERE id = ?",
                (appliance_id,),
            )
            messages.append(f"{name} is running.")

        picture = get_picture(connection)

    picture["message"] = " ".join(messages)
    return picture

@app.delete("/api/appliances/{appliance_id}")
def remove_appliance(appliance_id: int):
    with closing(sqlite3.connect(DB_PATH, timeout=10)) as connection, connection:
        connection.execute("BEGIN IMMEDIATE")
        cursor = connection.execute(
            "DELETE FROM appliances WHERE id = ?", (appliance_id,)
        )
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="Appliance not found")
        messages = ["Appliance removed."]
        messages.extend(restore_appliances(connection))
        picture = get_picture(connection)

    picture["message"] = " ".join(messages)
    return picture