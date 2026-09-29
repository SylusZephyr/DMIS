"use client";
// Optional 2D geography layer. Loaded (and the mapbox-gl bundle downloaded) only
// when NEXT_PUBLIC_MAPBOX_TOKEN is set at build time; otherwise the 3D globe is
// the only view. Plots the same real data as the globe: supplier countries and
// dataset marketplaces -- never inferred geography.
import { useEffect, useRef } from "react";
import "mapbox-gl/dist/mapbox-gl.css";
import type { FeatureCollection, Point } from "geojson";
import type { GeoCountry } from "@/components/three/globe";
import { OPPORTUNITY_RAMP } from "@/lib/colors";

export const MAPBOX_TOKEN = process.env.NEXT_PUBLIC_MAPBOX_TOKEN ?? "";

export default function MapboxLayer({ countries, onSelect }: { countries: GeoCountry[]; onSelect: (c: GeoCountry) => void }) {
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!MAPBOX_TOKEN || !box.current) return;
    let map: import("mapbox-gl").Map | null = null;
    let cancelled = false;
    (async () => {
      const mapboxgl = (await import("mapbox-gl")).default;
      if (cancelled || !box.current) return;
      mapboxgl.accessToken = MAPBOX_TOKEN;
      map = new mapboxgl.Map({ container: box.current, style: "mapbox://styles/mapbox/dark-v11", center: [40, 25], zoom: 1.3,
        projection: "mercator", attributionControl: true });
      const maxRev = Math.max(1, ...countries.map((c) => c.market_revenue));
      const maxSup = Math.max(1, ...countries.map((c) => c.suppliers));
      const data: FeatureCollection<Point, { iso2: string; activity: number; opportunity: number }> = {
        type: "FeatureCollection",
        features: countries.map((c) => ({
          type: "Feature", geometry: { type: "Point", coordinates: [c.lon, c.lat] },
          properties: { iso2: c.iso2, activity: Math.max(c.market_revenue / maxRev, c.suppliers / maxSup),
            opportunity: c.opportunity ?? -1 },
        })),
      };
      map.on("load", () => {
        if (!map) return;
        map.addSource("dmis-geo", { type: "geojson", data });
        map.addLayer({
          id: "dmis-geo", type: "circle", source: "dmis-geo",
          paint: {
            "circle-radius": ["interpolate", ["linear"], ["sqrt", ["get", "activity"]], 0, 5, 1, 26],
            // same sequential opportunity ramp as the globe; orange = suppliers only
            "circle-color": ["case", ["<", ["get", "opportunity"], 0], "#ff9f5a",
              ["interpolate", ["linear"], ["get", "opportunity"], ...OPPORTUNITY_RAMP.flatMap((s) => [s.v, s.c])]],
            "circle-opacity": 0.85, "circle-stroke-color": "#04070d", "circle-stroke-width": 2,
          },
        });
        map.on("click", "dmis-geo", (e) => {
          const iso = (e.features?.[0] as { properties?: { iso2?: string } } | undefined)?.properties?.iso2;
          const c = countries.find((x) => x.iso2 === iso);
          if (c) onSelect(c);
        });
        map.on("mouseenter", "dmis-geo", () => { if (map) map.getCanvas().style.cursor = "pointer"; });
        map.on("mouseleave", "dmis-geo", () => { if (map) map.getCanvas().style.cursor = ""; });
      });
    })();
    return () => { cancelled = true; map?.remove(); };
  }, [countries, onSelect]);
  return <div ref={box} className="absolute inset-0" />;
}
