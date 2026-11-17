import { useEffect, useState } from "react";

/** Hash routes, so the static build works behind any server without rewrites. */
export type Route =
  | { page: "home" }
  | { page: "logs" }
  | { page: "stats" }
  | { page: "analysis"; id?: string }
  | { page: "ingest" };

export function parseHash(hash: string): Route {
  const [page, id] = hash.replace(/^#\/?/, "").split("/");
  switch (page) {
    case "logs":
      return { page: "logs" };
    case "stats":
      return { page: "stats" };
    case "ingest":
      return { page: "ingest" };
    case "analysis":
      return id ? { page: "analysis", id: decodeURIComponent(id) } : { page: "analysis" };
    default:
      return { page: "home" };
  }
}

export function routeToHash(route: Route): string {
  if (route.page === "analysis" && route.id) return `#/analysis/${encodeURIComponent(route.id)}`;
  return `#/${route.page}`;
}

export function navigate(route: Route): void {
  window.location.hash = routeToHash(route);
}

export function useRoute(): Route {
  const [route, setRoute] = useState(() => parseHash(window.location.hash));
  useEffect(() => {
    const onChange = () => setRoute(parseHash(window.location.hash));
    window.addEventListener("hashchange", onChange);
    return () => window.removeEventListener("hashchange", onChange);
  }, []);
  return route;
}
