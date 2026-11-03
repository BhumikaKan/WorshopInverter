"use client";

import { useState, useCallback, useEffect } from "react";

export default function Home() {
  const [system, setSystem] = useState({
    appliances: [],
    capacity: 800,
    current_load: 0,
    remaining_capacity: 800,
  });
  const [loading, setLoading] = useState(true);
  const [tableError, setTableError] = useState("");
  const [message, setMessage] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [busyId, setBusyId] = useState(null);

  const loadAppliances = useCallback(async (signal) => {
    try {
      const response = await fetch("http://127.0.0.1:8001/api/state", {
        cache: "no-store",
        signal,
      });

      if (!response.ok) {
        throw new Error("Failed to load appliances.");
      }

      const data = await response.json();
      if (!signal.aborted) {
        setSystem(data);
        setTableError("");
      }
    } catch (error) {
      if (error.name !== "AbortError") {
        setTableError("Unable to refresh. The displayed data may be out of date.");
      }
    } finally {
      if (!signal.aborted) {
        setLoading(false);
      }
    }
  }, []);

  useEffect(() => {
    if (submitting || busyId !== null) {
      return;
    }

    const controller = new AbortController();
    let timer;

    async function refresh() {
      await loadAppliances(controller.signal);
      if (!controller.signal.aborted) {
        timer = window.setTimeout(refresh, 3000);
      }
    }

    refresh();

    return () => {
      controller.abort();
      window.clearTimeout(timer);
    };
  }, [loadAppliances, submitting, busyId]);

  async function handleSubmit(event) {
    event.preventDefault();
    const form = event.currentTarget;
    const formData = new FormData(form);
    const applianceData = {
      name: formData.get("name"),
      wattage: Number(formData.get("wattage")),
      priority: Number(formData.get("priority")),
    };

    setSubmitting(true);
    setMessage("");

    try {
      const response = await fetch("http://127.0.0.1:8001/api/applianceForm", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(applianceData),
      });

      const result = await response.json();
      if (!response.ok) {
        throw new Error("Could not register. Check the name, wattage and priority.");
      }

      setSystem(result);
      setMessage(result.message);
      form.reset();
    } catch (error) {
      setMessage(error.message);
    } finally {
      setSubmitting(false);
    }
  }
  async function changeState(applianceId, action) {
    setBusyId(applianceId);
    setMessage("");

    try {
      const response = await fetch(
        `http://127.0.0.1:8001/api/appliances/${applianceId}/${action}`,
        { method: "POST" }
      );

      const result = await response.json();
      if (!response.ok) {
        throw new Error(result.detail || "Unable to change appliance state.");
      }

      setSystem(result);
      setMessage(result.message);
    } catch (error) {
      setMessage(error.message);
    } finally {
      setBusyId(null);
    }
  }

  async function removeAppliance(applianceId) {
    const confirmed = window.confirm("Remove this appliance?");

    if (!confirmed) {
      return;
    }

    setBusyId(applianceId);
    setMessage("");

    try {
      const response = await fetch(
        `http://127.0.0.1:8001/api/appliances/${applianceId}`,
        { method: "DELETE" }
      );

      const result = await response.json();
      if (!response.ok) {
        throw new Error(result.detail || "Unable to remove appliance.");
      }

      setSystem(result);
      setMessage(result.message);
    } catch (error) {
      setMessage(error.message);
    } finally {
      setBusyId(null);
    }
  }

  return (
    <main className="mx-auto w-full max-w-6xl p-6">
      <h1 className="mb-4 text-2xl font-semibold">Inverter Load Manager</h1>

      <section className="mb-6" aria-label="Inverter capacity">
        <h2 className="mb-2 text-xl font-semibold">Capacity</h2>
        {loading ? (
          <p>Loading current load...</p>
        ) : (
          <>
            <p className="mb-2">
              {system.current_load}W used / {system.capacity}W total
              {" - "}{system.remaining_capacity}W free
            </p>
            <progress
              value={system.current_load}
              max={system.capacity}
              aria-label="Watts used"
              className="h-4 w-full accent-green-600"
            />
          </>
        )}
      </section>

      <p role="status" className="mb-4 whitespace-pre-line">{message}</p>
      {tableError && <p role="alert" className="mb-4 text-red-600">{tableError}</p>}

      <div className="grid grid-cols-1 items-start gap-8 lg:grid-cols-[320px_minmax(0,1fr)]">
        <form onSubmit={handleSubmit} className="flex min-w-0 flex-col gap-4">
          <h2 className="text-xl font-semibold">Register Appliance</h2>
          <label>
            Name
            <input name="name" required className="block w-full border p-2" />
          </label>
          <label>
            Wattage
            <input
              name="wattage"
              type="number"
              min="0.01"
              step="0.01"
              required
              className="block w-full border p-2"
            />
          </label>
          <label>
            Priority
            <input
              name="priority"
              type="number"
              min="1"
              step="1"
              required
              className="block w-full border p-2"
            />
          </label>
          <button
            type="submit"
            disabled={submitting || busyId !== null || loading}
            className="border p-2 disabled:opacity-50"
          >
            {submitting ? "Submitting..." : "Submit"}
          </button>
        </form>

        <section className="min-w-0">
          <h2 className="mb-4 text-xl font-semibold">Saved Appliances</h2>

          <div className="overflow-x-auto">
            <table className="w-full border-collapse text-left">
              <thead>
                <tr>
                  <th scope="col" className="border p-2">ID</th>
                  <th scope="col" className="border p-2">Name</th>
                  <th scope="col" className="border p-2">Wattage (W)</th>
                  <th scope="col" className="border p-2">Priority</th>
                  <th scope="col" className="border p-2">State</th>
                  <th scope="col" className="border p-2">Actions</th>
                </tr>
              </thead>
              <tbody>
                {!loading && system.appliances.length === 0 && (
                  <tr>
                    <td colSpan="6" className="border p-4">No appliances registered.</td>
                  </tr>
                )}
                {system.appliances.map((appliance) => {
                  let stateColor = "bg-zinc-100 text-zinc-800";
                  if (appliance.state === "running") {
                    stateColor = "bg-green-100 text-green-800";
                  }
                  if (appliance.state === "shed") {
                    stateColor = "bg-amber-100 text-amber-900";
                  }

                  return (
                  <tr key={appliance.id}>
                    <td className="border p-2">{appliance.id}</td>
                    <td className="border p-2">{appliance.name}</td>
                    <td className="border p-2">{appliance.wattage}</td>
                    <td className="border p-2">{appliance.priority}</td>
                    <td className="border p-2">
                      <span className={`inline-block px-2 py-1 capitalize ${stateColor}`}>
                        {appliance.state}
                      </span>
                    </td>
                    <td className="border p-2">
                      <div className="flex flex-wrap gap-2">
                      <button
                        type="button"
                        disabled={busyId !== null || submitting || appliance.state === "running"}
                        onClick={() => changeState(appliance.id, "on")}
                        className="border p-2 disabled:opacity-50"
                      >
                        On
                      </button>
                      <button
                        type="button"
                        disabled={busyId !== null || submitting || appliance.state === "off"}
                        onClick={() => changeState(appliance.id, "off")}
                        className="border p-2 disabled:opacity-50"
                      >
                        Off
                      </button>
                      <button
                        type="button"
                        disabled={busyId !== null || submitting}
                        onClick={() => removeAppliance(appliance.id)}
                        className="border p-2 disabled:opacity-50"
                      >
                        Remove
                      </button>
                      </div>
                    </td>
                  </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </section>
      </div>
    </main>
  );
}