import { useCallback, useEffect, useRef, useState } from "react";

export interface ApiState<T> {
  data: T | undefined;
  error: unknown;
  loading: boolean;
  reload: () => void;
}

/**
 * Run `load` whenever `key` changes (and on `reload`). Earlier requests are aborted, so a
 * slow response never overwrites a newer one.
 */
export function useApi<T>(load: (signal: AbortSignal) => Promise<T>, key: string): ApiState<T> {
  const [state, setState] = useState<{ data?: T; error?: unknown; loading: boolean }>({ loading: true });
  const [tick, setTick] = useState(0);
  const loadRef = useRef(load);
  loadRef.current = load;

  useEffect(() => {
    const controller = new AbortController();
    setState((s) => ({ ...s, loading: true, error: undefined }));
    loadRef.current(controller.signal).then(
      (data) => setState({ data, loading: false }),
      (error: unknown) => {
        if (controller.signal.aborted) return;
        setState((s) => ({ ...s, error, loading: false }));
      },
    );
    return () => controller.abort();
  }, [key, tick]);

  const reload = useCallback(() => setTick((t) => t + 1), []);
  return { data: state.data, error: state.error, loading: state.loading, reload };
}
