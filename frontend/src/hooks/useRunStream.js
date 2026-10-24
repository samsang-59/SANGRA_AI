// The live events of one run (05_frontend_design.md F6): opens the stream while the run is active, keeps the events
// in order, refreshes the run record on the events that change it, and reports the connection state.
import { useCallback, useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { openRunStream } from "../api/stream.js";
import { keys } from "./queries.js";

// Events that change what the run record shows (F6 "What each event does").
const REFRESH_ON = ["status_changed", "approval_requested", "hint_requested", "tests_finished"];
export const FLUSH_MS = 100; // many events at once are drawn together (FE5-L02)

/**
 * Use it only while the run is active, and give its component key={runId}, so that a new run starts from an empty list
 * (FE5-X06). connection: "connecting" | "live" | "reconnecting" | "lost" | "ended".
 */
export function useRunStream(runId) {
  const queryClient = useQueryClient();
  const [events, setEvents] = useState([]);
  const [connection, setConnection] = useState("connecting");
  const [attempt, setAttempt] = useState(0); // a new value opens the stream again (Retry)
  const buffer = useRef([]);
  const timer = useRef(null);
  const seen = useRef(new Set());

  const refreshRun = useCallback(() => {
    queryClient.invalidateQueries({ queryKey: keys.run(runId) });
  }, [queryClient, runId]);

  useEffect(() => {
    let stopped = false;
    const flush = () => {
      timer.current = null;
      if (stopped || !buffer.current.length) return;
      const batch = buffer.current;
      buffer.current = [];
      setEvents((previous) => [...previous, ...batch]);
    };
    const stop = openRunStream(runId, (event) => {
      if (seen.current.has(event.sequence_number)) return; // a replay after Retry
      seen.current.add(event.sequence_number);
      buffer.current.push(event);
      if (!timer.current) timer.current = setTimeout(flush, FLUSH_MS);
      if (REFRESH_ON.includes(event.event_type)) refreshRun();
    }, {
      onOpen: () => setConnection("live"),
      onReconnecting: () => setConnection("reconnecting"),
      onEnd: () => {
        flush();
        setConnection("ended");
        refreshRun();
        queryClient.invalidateQueries({ queryKey: keys.activeRun });
        queryClient.invalidateQueries({ queryKey: ["runs"] });
      },
      onError: (error) => {
        flush();
        if (error.code === "NOT_FOUND") refreshRun(); // the run was deleted: the page shows "not found"
        setConnection("lost");
      },
    });
    return () => {
      stopped = true;
      stop();
      clearTimeout(timer.current);
      timer.current = null;
      buffer.current = [];
    };
  }, [runId, attempt, refreshRun, queryClient]);

  const retry = useCallback(() => {
    setConnection("connecting");
    refreshRun();
    setAttempt((n) => n + 1);
  }, [refreshRun]);

  // Back from a sleeping computer or a hidden tab with a lost connection: catch up (FE5-X11).
  useEffect(() => {
    const onVisible = () => {
      if (document.visibilityState === "visible" && connection === "lost") retry();
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => document.removeEventListener("visibilitychange", onVisible);
  }, [connection, retry]);

  return { events, connection, retry };
}
